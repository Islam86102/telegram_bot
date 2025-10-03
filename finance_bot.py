import os
import sqlite3
import tempfile
import io
from datetime import datetime, date
import matplotlib.pyplot as plt
import logging

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ----------------- Настройки -----------------
TOKEN = "7940613188:AAEyklD7Sa8uY0adjfnM5Hsgg_LfMEJ7Gb4"  # <--- вставь сюда токен
DB_FILE = "finance.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------- База данных -----------------
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS finance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    type TEXT,        -- 'income' или 'expense'
    amount REAL,
    category TEXT,
    date TEXT         -- YYYY-MM-DD
)
""")
conn.commit()

# ----------------- Главное меню (ReplyKeyboard) -----------------
main_menu = [
    ["➕ Доход", "➖ Расход"],
    ["💰 Баланс", "📊 Отчет"],
    ["📒 История"]
]
reply_markup = ReplyKeyboardMarkup(main_menu, resize_keyboard=True)

# ----------------- Помощники -----------------
def parse_amount_and_category(text: str):
    """
    Принимает строки:
      "1000 еда", "+1000 еда", "-500 транспорт", "1000"
    Возвращает (sign, amount(float), category_str)
    sign: 1 for positive, -1 for negative, 0 if not provided
    """
    text = text.strip()
    if not text:
        raise ValueError("Пустая строка")

    # Если начинается с + или -, учитываем
    sign = 0
    if text[0] == "+":
        sign = 1
        text = text[1:].strip()
    elif text[0] == "-":
        sign = -1
        text = text[1:].strip()

    parts = text.split(maxsplit=1)
    amount_str = parts[0].replace(",", ".")
    amount = float(amount_str)  # вызовет ValueError если не число
    category = parts[1].strip() if len(parts) > 1 else "Без категории"
    return sign, amount, category

def insert_record(user_id: int, rec_type: str, amount: float, category: str, rec_date: str = None):
    if rec_date is None:
        rec_date = date.today().isoformat()
    cur.execute(
        "INSERT INTO finance (user_id, type, amount, category, date) VALUES (?, ?, ?, ?, ?)",
        (user_id, rec_type, amount, category, rec_date)
    )
    conn.commit()
    logger.info("Inserted record: user=%s type=%s amount=%s category=%s date=%s", user_id, rec_type, amount, category, rec_date)

def get_balance(user_id: int):
    cur.execute("SELECT SUM(amount) FROM finance WHERE user_id=? AND type='income'", (user_id,))
    inc = cur.fetchone()[0] or 0
    cur.execute("SELECT SUM(amount) FROM finance WHERE user_id=? AND type='expense'", (user_id,))
    exp = cur.fetchone()[0] or 0
    return inc - exp, inc, exp

# ----------------- Графики -----------------
def build_daily_series(user_id: int, kind: str, month_mask: str):
    """
    Возвращает пары (dates_sorted, values) для заданного месяца_mask 'YYYY-MM'
    """
    cur.execute(
        "SELECT date, SUM(amount) FROM finance WHERE user_id=? AND type=? AND date LIKE ? GROUP BY date ORDER BY date",
        (user_id, kind, month_mask + "%"),
    )
    rows = cur.fetchall()  # список (date, sum)
    dates = [r[0] for r in rows]
    values = [r[1] for r in rows]
    return dates, values

def plot_to_file(dates, values, title):
    plt.figure(figsize=(8, 4))
    plt.plot(dates, values, marker="o")
    plt.title(title)
    plt.xlabel("Дата")
    plt.ylabel("Сумма")
    plt.xticks(rotation=40)
    plt.tight_layout()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    plt.savefig(tmp.name)
    plt.close()
    return tmp.name

# ----------------- Хендлеры -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! 👋 Это финансовый бот.\n\n"
        "Нажми кнопку для действия или введи напрямую:\n"
        "  +1000 зарплата  (или) 1000 зарплата\n"
        "  -500 еда",
        reply_markup=reply_markup
    )

# Обработка нажатий меню (кнопки отправляют текст)
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    user_id = update.message.from_user.id

    if txt == "➕ Доход":
        context.user_data['pending'] = 'income'
        await update.message.reply_text("Введите сумму и категорию для дохода (пример: 1000 зарплата)")

    elif txt == "➖ Расход":
        context.user_data['pending'] = 'expense'
        await update.message.reply_text("Введите сумму и категорию для расхода (пример: 500 еда)")

    elif txt == "💰 Баланс":
        bal, inc, exp = get_balance(user_id)
        await update.message.reply_text(f"💰 Баланс: {bal}\n➕ Доходы: {inc}\n➖ Расходы: {exp}", reply_markup=reply_markup)

    elif txt == "📒 История":
        # построим историю и кнопки редактирования/удаления
        cur.execute("SELECT id, type, amount, category, date FROM finance WHERE user_id=? ORDER BY date DESC, id DESC", (user_id,))
        rows = cur.fetchall()
        if not rows:
            await update.message.reply_text("История пуста.", reply_markup=reply_markup)
            return
        text_lines = []
        buttons = []
        for r in rows:
            rid, rtype, amount, category, rdate = r
            sign = "+" if rtype == "income" else "-"
            text_lines.append(f"{rid}. {rdate} {sign}{amount} ({category})")
            buttons.append([InlineKeyboardButton(f"🗑 Удалить {rid}", callback_data=f"del_{rid}"),
                            InlineKeyboardButton(f"✏️ Редактировать {rid}", callback_data=f"edit_{rid}")])
        await update.message.reply_text("\n".join(text_lines), reply_markup=InlineKeyboardMarkup(buttons))

    elif txt == "📊 Отчет":
        month_mask = date.today().strftime("%Y-%m")
        # расходы
        d_exp, v_exp = build_daily_series(user_id, 'expense', month_mask)
        # доходы
        d_inc, v_inc = build_daily_series(user_id, 'income', month_mask)

        await update.message.reply_text(f"📊 Отчет за {month_mask}:")
        # если нет данных, сообщаем
        if not d_exp and not d_inc:
            await update.message.reply_text("Нет операций в этом месяце.", reply_markup=reply_markup)
            return
        # график расходов
        if d_exp:
            fname = plot_to_file(d_exp, v_exp, f"Расходы по дням ({month_mask})")
            with open(fname, "rb") as f:
                await update.message.reply_photo(photo=f)
            try: os.remove(fname)
            except: pass
        else:
            await update.message.reply_text("Нет расходов в этом месяце.")

        # график доходов
        if d_inc:
            fname2 = plot_to_file(d_inc, v_inc, f"Доходы по дням ({month_mask})")
            with open(fname2, "rb") as f:
                await update.message.reply_photo(photo=f)
            try: os.remove(fname2)
            except: pass

        await update.message.reply_text("Готово.", reply_markup=reply_markup)

    else:
        # если текст не из меню — попадёт в общий обработчик ниже (но этот хендлер сработает только для точных совпадений;
        # чтобы не мешать, мы используем фильтр для меню при регистрации)
        await update.message.reply_text("Непонятная команда. Нажми кнопку меню или введи +1000 зарплата.", reply_markup=reply_markup)

# Общий обработчик текстовых сообщений:
# 1) если есть pending (ожидание суммы) — добавляем запись
# 2) если есть editing (ожидание новой суммы для редактирования) — обновляем запись
# 3) иначе пробуем распарсить прямой ввод '+1000 еда' или '-500 еда'
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    user_id = update.message.from_user.id

    # Если мы в режиме редактирования конкретной записи
    if 'editing' in context.user_data:
        rid = context.user_data.pop('editing')
        try:
            _, amount, category = parse_amount_and_category(txt)
            # получим тип записи (income/expense) чтобы сохранить тип
            cur.execute("SELECT user_id FROM finance WHERE id=?", (rid,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("Запись не найдена.")
                return
            cur.execute("UPDATE finance SET amount=?, category=? WHERE id=?", (amount, category, rid))
            conn.commit()
            await update.message.reply_text(f"Запись #{rid} обновлена: {amount} ({category})", reply_markup=reply_markup)
        except Exception as e:
            await update.message.reply_text("Неверный формат при редактировании. Формат: 1000 категория")
        return

    # Если ранее нажали кнопку "➕ Доход" или "➖ Расход"
    if 'pending' in context.user_data:
        action = context.user_data.pop('pending')  # 'income' или 'expense'
        try:
            # пользователь может ввести "+1000 еда" или "1000 еда"
            sign, amount, category = parse_amount_and_category(txt)
            # если пользователь ввел со знаком и он противоречит pending, игнорируем знак и используем pending
            # используем amount как положительное число
            insert_record(user_id, action, amount, category)
            await update.message.reply_text(f"✅ Запись добавлена: {('+' if action=='income' else '-')}{amount} ({category})", reply_markup=reply_markup)
        except Exception as e:
            await update.message.reply_text("Неверный формат. Введите: 1000 категория (пример: 1000 зарплата)")
        return

    # Попробуем распарсить прямой ввод типа "+1000 еда" или "-500 еда"
    try:
        sign, amount, category = parse_amount_and_category(txt)
        if sign == 1:
            insert_record(user_id, 'income', amount, category)
            await update.message.reply_text(f"✅ Доход +{amount} ({category}) добавлен", reply_markup=reply_markup)
            return
        elif sign == -1:
            insert_record(user_id, 'expense', amount, category)
            await update.message.reply_text(f"✅ Расход -{amount} ({category}) добавлен", reply_markup=reply_markup)
            return
    except Exception:
        pass

    # иначе непонятный ввод
    await update.message.reply_text("Не понял. Нажми кнопку меню или введи '+1000 зарплата' / '-500 еда'.")

# CallbackQuery: удаление и редактирование
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data.startswith("del_"):
        rid = int(data.split("_", 1)[1])
        # проверим право удаления (владелец записи)
        cur.execute("SELECT user_id FROM finance WHERE id=?", (rid,))
        row = cur.fetchone()
        if not row:
            await query.edit_message_text("Запись не найдена.")
            return
        owner_id = row[0]
        if owner_id != user_id:
            await query.edit_message_text("Вы не можете удалять чужие записи.")
            return
        cur.execute("DELETE FROM finance WHERE id=?", (rid,))
        conn.commit()
        await query.edit_message_text(f"✅ Запись #{rid} удалена.")

    elif data.startswith("edit_"):
        rid = int(data.split("_", 1)[1])
        cur.execute("SELECT user_id, type, amount, category FROM finance WHERE id=?", (rid,))
        row = cur.fetchone()
        if not row:
            await query.edit_message_text("Запись не найдена.")
            return
        owner_id = row[0]
        if owner_id != user_id:
            await query.edit_message_text("Вы не можете редактировать чужие записи.")
            return
        # пометим, что ждем нового значения для этой записи
        context.user_data['editing'] = rid
        await query.edit_message_text(f"Введите новую сумму и категорию для записи #{rid} (пример: 500 такси)")

# ----------------- Регистрация и запуск -----------------
def main():
    app = Application.builder().token(TOKEN).build()

    # Команда старт
    app.add_handler(CommandHandler("start", start))

    # Меню — ловим точные нажатия клавиатуры
    app.add_handler(MessageHandler(filters.Regex('^(➕ Доход|➖ Расход|💰 Баланс|📊 Отчет|📒 История)$') & filters.USER, menu_handler))

    # Callback кнопки (удалить/редактировать)
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Общий текстовый обработчик (всё остальное)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("Бот запущен. Ctrl+C для остановки.")
    app.run_polling()

if __name__ == "__main__":
    main()

