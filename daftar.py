import logging
import datetime
import pytz
import os
import asyncio
import re
from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    PicklePersistence,
)

# ----------------------
# Logging & Config
# ----------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

token = os.environ.get("TOKEN") or "YOUR_BOT_TOKEN_HERE"
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")
WEBHOOK_PATH = f"/{token}"
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None
PORT = int(os.environ.get("PORT", 8000))
timezone = pytz.timezone(os.environ.get("TZ", "Asia/Jakarta"))

# ----------------------
# Schedule & Constants
# ----------------------
TARGET_CHAT_ID = -1003785049591
TARGET_THREAD_ID = 2

SUBMENUS = ["DWT", "BG", "DWL", "NG", "TG88", "TTGL", "KTT", "TTGG"]
OWNER_USERNAME = "Intan_Payungggg"

TIMES = {
    "pagi":  ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"],
    "siang": ["16:00", "17:00", "18:00", "19:00", "20:00", "21:00", "22:00"],
    "malam": ["00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00"],
}

RESET_TIMES =   {"pagi": "07:00", "siang": "15:00", "malam": "23:00"}
PREP_TIMES =    {"pagi": "07:50", "siang": "15:50", "malam": "23:50"}
SUMMARY_TIMES = {"pagi": "14:30", "siang": "22:30", "malam": "06:30"}

SHIFTS_ORDER = ["pagi", "malam", "siang"]
EPOCH_DATE = datetime.date(2026, 3, 23)

# ----------------------
# Helper Functions
# ----------------------
def is_allowed(update: Update) -> bool:
    if not update.effective_chat or not update.message:
        return False
    return (update.effective_chat.id == TARGET_CHAT_ID and 
            update.message.message_thread_id == TARGET_THREAD_ID)

def get_shift_info(now: datetime.datetime):
    if now.hour < 7:
        logical_now = now - datetime.timedelta(days=1)
    else:
        logical_now = now
        
    logical_date = logical_now.date()
    days_diff = (logical_date - EPOCH_DATE).days
    weeks_passed = days_diff // 7
    
    current_shift = SHIFTS_ORDER[weeks_passed % 3]
    return current_shift, logical_date

def get_target_datetime(jam_str: str, now: datetime.datetime) -> datetime.datetime:
    target_hour, target_minute = map(int, jam_str.split(':'))
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    
    if now.hour == 23 and target_hour < 7:
        target += datetime.timedelta(days=1)
    elif now.hour < 7 and target_hour >= 23:
        target -= datetime.timedelta(days=1)
        
    return target

# ----------------------
# Tampilan Jadwal Teks Rapi
# ----------------------
async def send_schedule_to_chat(bot, chat_id, chat_data, waktu):
    thread_id = chat_data.get("thread_id") or TARGET_THREAD_ID
    skips = chat_data.get("skips", set())
    
    lines = [f"📋 *Jadwal Shift {waktu.capitalize()}*"]
    
    for sec in SUBMENUS:
        lines.append(f"\n🔹 *{sec}*")
        jadwal = TIMES[waktu]
        row1, row2 = [], []
        
        # Membagi jadwal menjadi 2 baris agar kotak dan simetris
        for i, j in enumerate(jadwal):
            sym = "✅" if f"{sec}_{j}" in skips else "❌"
            item = f"{j} {sym}"
            if i < 4:
                row1.append(item)
            else:
                row2.append(item)
                
        lines.append(" | ".join(row1))
        if row2:
            lines.append(" | ".join(row2))

    text = "\n".join(lines)

    try:
        await bot.send_message(
            chat_id=chat_id, message_thread_id=thread_id, text=text, parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Gagal kirim pesan jadwal: {e}")

# ----------------------
# Sistem Otomatis (JobQueue)
# ----------------------
async def job_reset(context: ContextTypes.DEFAULT_TYPE):
    cid = context.job.data["chat_id"]
    shift = context.job.data["shift"]
    chat_data = context.application.chat_data.get(cid)
    if chat_data is not None:
        chat_data["skips"] = set()
        chat_data["history"] = []
    logger.info(f"🔄 Auto-Reset shift {shift} grup {cid}.")

async def job_persiapan(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    try:
        await context.bot.send_message(
            chat_id=d["chat_id"], message_thread_id=d["thread_id"],
            text=f"🌅 *PERSIAPAN SHIFT {d['shift'].upper()}*\n\nSilakan mulai mengirimkan laporan. Ketik /jadwal untuk melihat status rekap.", 
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error persiapan: {e}")

async def job_mulai(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    try:
        await context.bot.send_message(
            chat_id=d["chat_id"], message_thread_id=d["thread_id"], 
            text=f"🔔 Waktu pelaporan jadwal *{d['jam']}* dimulai!", parse_mode="Markdown"
        )
    except Exception:
        pass

async def job_peringatan(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    chat_data = context.application.chat_data.get(d["chat_id"], {})
    skips = chat_data.get("skips", set())
    missing = [s for s in SUBMENUS if f"{s}_{d['jam']}" not in skips]
    
    if missing:
        try:
            await context.bot.send_message(
                chat_id=d["chat_id"], message_thread_id=d["thread_id"],
                text=f"⚠️ *PERINGATAN!* 10 Menit menuju batas akhir laporan *{d['jam']}*.\nBelum lapor: {', '.join(missing)}", 
                parse_mode="Markdown"
            )
        except Exception:
            pass

async def job_rekap(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    chat_data = context.application.chat_data.get(d["chat_id"], {})
    skips = chat_data.get("skips", set())
    terlewat = []
    
    for sec in SUBMENUS:
        for j in TIMES[d['shift']]:
            if f"{sec}_{j}" not in skips: 
                terlewat.append(f"❌ {sec} - {j}")
                
    admin_tags = "@cartenz88 @Agha1104 @Gemini_Squad"
    if terlewat:
        msg = (f"📊 <b>RINGKASAN AKHIR SHIFT {d['shift'].upper()}</b>\n\n"
               f"Terdapat jadwal laporan yang <b>TERLEWAT</b>:\n"
               f"{chr(10).join(terlewat)}\n\n"
               f"Halo {admin_tags}, mohon bantuannya untuk menindaklanjuti. 🙏")
    else:
        msg = (f"📊 <b>RINGKASAN AKHIR SHIFT {d['shift'].upper()}</b>\n\n"
               f"Laporan hari ini <b>SEMPURNA!</b> 🎉 Seluruh jadwal telah dilaporkan.\n\n"
               f"Halo {admin_tags}, operasional berjalan lancar tanpa kendala. 🙏")
               
    try:
        await context.bot.send_message(chat_id=d["chat_id"], message_thread_id=d["thread_id"], text=msg, parse_mode="HTML")
    except Exception:
        pass

async def job_rotator(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    schedule_group_jobs(context.job_queue, d["chat_id"], d["thread_id"])

def schedule_group_jobs(job_queue, chat_id, thread_id):
    for job in job_queue.jobs():
        if job.name and job.name.endswith(f"_{chat_id}") and not job.name.startswith("rotator_"):
            job.schedule_removal()

    now = datetime.datetime.now(timezone)
    check_time = now + datetime.timedelta(hours=1) if (now.hour == 6 and now.minute >= 45) else now
    current_shift, _ = get_shift_info(check_time)
    
    rh, rm = map(int, RESET_TIMES[current_shift].split(':'))
    job_queue.run_daily(job_reset, time=datetime.time(hour=rh, minute=rm, tzinfo=timezone), name=f"reset_{current_shift}_{chat_id}", data={"chat_id": chat_id, "shift": current_shift})

    ph, pm = map(int, PREP_TIMES[current_shift].split(':'))
    job_queue.run_daily(job_persiapan, time=datetime.time(hour=ph, minute=pm, tzinfo=timezone), name=f"prep_{current_shift}_{chat_id}", data={"chat_id": chat_id, "thread_id": thread_id, "shift": current_shift})

    sh, sm = map(int, SUMMARY_TIMES[current_shift].split(':'))
    job_queue.run_daily(job_rekap, time=datetime.time(hour=sh, minute=sm, tzinfo=timezone), name=f"rekap_{current_shift}_{chat_id}", data={"chat_id": chat_id, "thread_id": thread_id, "shift": current_shift})

    for jam in TIMES[current_shift]:
        h, m = map(int, jam.split(':'))
        job_queue.run_daily(job_mulai, time=datetime.time(hour=h, minute=m, tzinfo=timezone), name=f"start_{jam}_{chat_id}", data={"chat_id": chat_id, "thread_id": thread_id, "jam": jam})
        
        warn_m = m + 20
        warn_h = h
        if warn_m >= 60:
            warn_h = (warn_h + 1) % 24
            warn_m -= 60
        job_queue.run_daily(job_peringatan, time=datetime.time(hour=warn_h, minute=warn_m, tzinfo=timezone), name=f"warn_{jam}_{chat_id}", data={"chat_id": chat_id, "thread_id": thread_id, "jam": jam, "shift": current_shift})

    rotator_name = f"rotator_{chat_id}"
    if not any(j.name == rotator_name for j in job_queue.jobs()):
        job_queue.run_daily(job_rotator, time=datetime.time(hour=6, minute=50, tzinfo=timezone), name=rotator_name, data={"chat_id": chat_id, "thread_id": thread_id})

# ----------------------
# Command Handlers
# ----------------------
async def say_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    
    user = update.effective_user
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id
    
    member = await context.bot.get_chat_member(chat_id, user.id)
    is_admin = member.status in ["administrator", "creator"]
    is_owner = user.username and user.username.lower() == OWNER_USERNAME.lower()

    if not (is_admin or is_owner):
        return 

    if not context.args:
        return

    pesan = " ".join(context.args)
    try: await update.message.delete()
    except: pass

    await context.bot.send_message(chat_id=chat_id, message_thread_id=thread_id, text=pesan)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    await update.message.reply_text("👋 Bot Jadwal Siap beroperasi dengan Mode Ringan (Teks Biasa)!")

async def aktifkan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    
    cid = update.effective_chat.id
    thread_id = update.message.message_thread_id
    active = context.bot_data.setdefault("active_groups", set())
    active.add(cid)
    context.bot_data["active_groups"] = active
    context.chat_data["thread_id"] = thread_id
    schedule_group_jobs(context.job_queue, cid, thread_id)
    
    current_shift, _ = get_shift_info(datetime.datetime.now(timezone))
    await update.message.reply_text(f"✅ Sistem DIAKTIFKAN.\nShift: *{current_shift.upper()}*", parse_mode="Markdown")

async def nonaktifkan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    
    cid = update.effective_chat.id
    active = context.bot_data.get("active_groups", set())
    if cid in active:
        active.remove(cid)
        context.bot_data["active_groups"] = active
        for job in context.job_queue.jobs():
            if job.name and job.name.endswith(f"_{cid}"):
                job.schedule_removal()
        await update.message.reply_text("⛔ *Bot Dinonaktifkan!*", parse_mode="Markdown")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    
    cid = update.effective_chat.id
    now = datetime.datetime.now(timezone)
    current_shift, _ = get_shift_info(now)
    is_active = "Aktif ✅" if cid in context.bot_data.get("active_groups", set()) else "Nonaktif ❌"
    await update.message.reply_text(f"📡 *STATUS BOT*\nStatus: {is_active}\nShift: *{current_shift.upper()}*", parse_mode="Markdown")

async def jadwal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fungsi dipanggil khusus hanya jika diketik /jadwal"""
    if not is_allowed(update): return
    
    cid = update.effective_chat.id
    if cid not in context.bot_data.get("active_groups", set()): return
    current_shift, _ = get_shift_info(datetime.datetime.now(timezone))
    await send_schedule_to_chat(context.bot, cid, context.chat_data, current_shift)

async def rekap_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    
    cid = update.effective_chat.id
    if cid not in context.bot_data.get("active_groups", set()): return
    current_shift, _ = get_shift_info(datetime.datetime.now(timezone))
    skips = context.chat_data.get("skips", set())
    terlewat = [f"❌ {sec} - {j}" for sec in SUBMENUS for j in TIMES[current_shift] if f"{sec}_{j}" not in skips]
    msg = f"📊 *REKAP {current_shift.upper()}*\n" + (chr(10).join(terlewat) if terlewat else "Laporan SEMPURNA! 🎉")
    await update.message.reply_text(msg, parse_mode="Markdown")

# ----------------------
# Tombol Manual & Auto Check
# ----------------------
async def manual_toggle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    user = update.effective_user
    is_owner = user.username and user.username.lower() == OWNER_USERNAME.lower()
    
    if not is_owner:
        await update.message.reply_text("❌ Anda tidak memiliki izin untuk menggunakan perintah ini.")
        return
        
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("⚠️ Format salah!\nGunakan: `/centang BRAND JAM` atau `/batal BRAND JAM`\nContoh: `/centang KTT 16:00`", parse_mode="Markdown")
        return
        
    sec, jam = args[0].upper(), args[1]
    chat_data = context.chat_data
    skips = chat_data.setdefault("skips", set())
    key = f"{sec}_{jam}"
    
    now = datetime.datetime.now(timezone)
    current_shift, _ = get_shift_info(now)
    
    if sec not in SUBMENUS or jam not in TIMES[current_shift]:
        await update.message.reply_text(f"❌ Tidak ada jadwal untuk {sec} jam {jam} di shift {current_shift.upper()}.")
        return

    cmd = update.message.text.split()[0].lower()
    if cmd == "/centang":
        skips.add(key)
        await update.message.reply_text(f"✅ Jadwal {sec} {jam} berhasil dicentang manual.")
    elif cmd == "/batal":
        skips.discard(key)
        await update.message.reply_text(f"❌ Jadwal {sec} {jam} berhasil dibatalkan.")

async def auto_check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return

    msg = update.message
    if not msg or not (msg.text or msg.caption): return
    chat_id = update.effective_chat.id
    if chat_id not in context.bot_data.get("active_groups", set()): return

    text = (msg.text or msg.caption).upper()
    if "TEST DAFTAR" in text:
        if not msg.photo and not msg.document:
            await msg.reply_text("❌ Wajib lampirkan foto/file!")
            return

        brand_match = re.search(r'BRAND\s*:\s*([A-Z0-9]+)', text)
        waktu_match = re.search(r'WAKTU\s*:\s*(\d{2}:\d{2})', text)

        if brand_match and waktu_match:
            sec, jam = brand_match.group(1).strip(), waktu_match.group(1).strip()
            now = datetime.datetime.now(timezone)
            
            target_dt = get_target_datetime(jam, now)
            start_window = target_dt - datetime.timedelta(minutes=10)
            end_window = target_dt + datetime.timedelta(minutes=30)
            
            if not (start_window <= now <= end_window):
                if now < start_window:
                    await msg.reply_text(f"⏳ Terlalu cepat! Laporan {jam} baru bisa dikirim mulai {start_window.strftime('%H:%M')}.")
                else:
                    await msg.reply_text(f"⏰ Terlambat! Laporan {jam} sudah ditutup (Maksimal {end_window.strftime('%H:%M')}).")
                return

            current_shift, _ = get_shift_info(now)
            if sec in SUBMENUS and jam in TIMES[current_shift]:
                key = f"{sec}_{jam}"
                skips = context.chat_data.setdefault("skips", set())
                if key not in skips:
                    skips.add(key)
                    # Hanya menjawab pesan yang masuk tanpa memanggil/memperbarui papan jadwal
                    await msg.reply_text(f"✅ Laporan {sec} {jam} Diterima!")

# ----------------------
# Main
# ----------------------
async def on_startup(app: Application):
    active = app.bot_data.setdefault("active_groups", set())
    
    if TARGET_CHAT_ID not in active:
        active.add(TARGET_CHAT_ID)
        app.bot_data["active_groups"] = active
        
    chat_data = app.chat_data.setdefault(TARGET_CHAT_ID, {})
    if "thread_id" not in chat_data:
        chat_data["thread_id"] = TARGET_THREAD_ID

    for cid in active:
        tid = app.chat_data.get(cid, {}).get("thread_id") or TARGET_THREAD_ID
        schedule_group_jobs(app.job_queue, cid, tid)

async def handle_root(request): return web.Response(text="Running")
async def handle_webhook(request):
    app = request.app['application']
    await app.update_queue.put(Update.de_json(await request.json(), app.bot))
    return web.Response()

async def main():
    persistence = PicklePersistence(filepath="bot_jadwal_data.pickle")
    app = ApplicationBuilder().token(token).persistence(persistence).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("aktifkan", aktifkan_cmd))
    app.add_handler(CommandHandler("nonaktifkan", nonaktifkan_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("jadwal", jadwal_cmd))
    app.add_handler(CommandHandler("rekap", rekap_cmd))
    app.add_handler(CommandHandler("say", say_cmd))
    
    app.add_handler(CommandHandler("centang", manual_toggle_cmd))
    app.add_handler(CommandHandler("batal", manual_toggle_cmd))

    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, auto_check_message))

    web_app = web.Application()
    web_app['application'] = app
    web_app.add_routes([web.get('/', handle_root), web.post(WEBHOOK_PATH, handle_webhook)])

    if WEBHOOK_URL: await app.bot.set_webhook(WEBHOOK_URL)
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

    await app.initialize()
    await app.start()
    while True: await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())
