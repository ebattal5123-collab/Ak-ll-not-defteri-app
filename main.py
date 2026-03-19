import threading
import time
from datetime import datetime, timezone, timedelta
from flask import Flask, request, redirect, url_for, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import os
import sys

# ----- Saat dilimi desteği (öncelikle zoneinfo dene, yoksa pytz, yoksa UTC varsay) -----
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Istanbul")
    USE_ZONEINFO = True
    print("✅ zoneinfo kullanılıyor")
except ImportError:
    try:
        import pytz
        TZ = pytz.timezone("Europe/Istanbul")
        USE_ZONEINFO = False
        print("✅ pytz kullanılıyor")
    except ImportError:
        print("⚠️  Saat dilimi kütüphanesi bulunamadı, UTC kullanılacak.")
        TZ = None

# ----- Bildirim kütüphanesi (plyer) -----
try:
    from plyer import notification
    PLYER_AVAILABLE = True
    print("✅ plyer kullanılıyor")
except ImportError:
    PLYER_AVAILABLE = False
    print("⚠️ Plyer bulunamadı, bildirimler konsola yazdırılacak.")

app = Flask(__name__)
app.secret_key = 'cok-gizli-bir-anahtar-buraya-yazin'   # production'da .env'den alın

# ----- Neon PostgreSQL bağlantısı -----
DB_URL = "postgresql://neondb_owner:npg_fnMEW5ylpsN1@ep-young-meadow-amg7vwmg-pooler.c-5.us-east-1.aws.neon.tech/neondb?sslmode=require"

def get_db_connection():
    return psycopg2.connect(DB_URL)

def init_db():
    """Tabloları oluştur (yoksa)"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(80) UNIQUE NOT NULL,
            password_hash VARCHAR(200) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(200) NOT NULL,
            content TEXT,
            reminder_time TIMESTAMP,   -- UTC olarak saklanacak
            is_notified BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("✅ Veritabanı tabloları hazır.")

def local_to_utc(local_dt):
    """Yerel datetime'ı UTC'ye çevirir."""
    if local_dt is None:
        return None
    if TZ is None:
        # dönüşüm yapılamıyor, olduğu gibi kabul et (riskli)
        return local_dt
    try:
        if USE_ZONEINFO:
            # zoneinfo ile
            return local_dt.replace(tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)
        else:
            # pytz ile
            return TZ.localize(local_dt).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception as e:
        print(f"⚠️ Saat dönüşüm hatası: {e}, UTC olarak kabul ediliyor.")
        return local_dt

# ----- BİLDİRİM KONTROL THREAD'İ -----
def check_reminders():
    """Her 20 saniyede bir çalışır, zamanı gelen bildirimleri gönderir."""
    print("🔄 Bildirim kontrol thread'i başlatıldı (20 sn aralıkla).")
    while True:
        try:
            conn = get_db_connection()
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            # reminder_time UTC olduğu için NOW() (UTC) ile karşılaştır
            cur.execute("""
                SELECT n.id, n.title, n.content, u.username
                FROM notes n
                JOIN users u ON n.user_id = u.id
                WHERE n.reminder_time <= NOW() AND n.is_notified = FALSE
            """)
            due_notes = cur.fetchall()
            if due_notes:
                print(f"⏰ {len(due_notes)} adet zamanı gelmiş not bulundu.")
            for note in due_notes:
                # Bildirim gönder
                if PLYER_AVAILABLE:
                    try:
                        notification.notify(
                            title=f"🔔 {note['title']}",
                            message=f"{note['username']}: {note['content'][:100]}",
                            timeout=5
                        )
                        print(f"✅ Bildirim gönderildi: {note['title']}")
                    except Exception as e:
                        print(f"❌ Plyer hatası: {e}")
                else:
                    # Konsola yaz (geliştirme için)
                    print(f"\n[ BİLDİRİM ] {note['title']} - {note['username']}\n{note['content']}\n")
                
                # Bildirildi olarak işaretle
                cur.execute("UPDATE notes SET is_notified = TRUE WHERE id = %s", (note['id'],))
                conn.commit()
            
            cur.close()
            conn.close()
        except Exception as e:
            print(f"❌ Kontrol döngüsü hatası: {e}")
        time.sleep(20)

# Thread'i başlat
if True:   # Her zaman başlat
    thread = threading.Thread(target=check_reminders, daemon=True)
    thread.start()

# ----- ROTALAR -----
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('notes'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            return "Kullanıcı adı ve şifre gerekli."
        hashed = generate_password_hash(password)
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s)", (username, hashed))
            conn.commit()
            return redirect(url_for('login'))
        except psycopg2.IntegrityError:
            return "Bu kullanıcı adı zaten mevcut."
        finally:
            cur.close()
            conn.close()
    # Kayıt formu (HTML)
    return '''
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <title>Kayıt Ol - Akıllı Notlar</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
            body {
                background: linear-gradient(145deg, #0b1120 0%, #19233c 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }
            .card {
                background: rgba(18, 25, 40, 0.9);
                backdrop-filter: blur(10px);
                border-radius: 32px;
                padding: 48px 40px;
                width: 100%;
                max-width: 440px;
                box-shadow: 0 30px 60px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(90, 140, 231, 0.2);
                animation: float 0.8s ease-out;
            }
            @keyframes float {
                0% { opacity: 0; transform: translateY(30px); }
                100% { opacity: 1; transform: translateY(0); }
            }
            h2 {
                color: white;
                font-size: 32px;
                font-weight: 500;
                margin-bottom: 8px;
                display: flex;
                align-items: center;
                gap: 12px;
            }
            h2 span {
                background: #2d3d5c;
                padding: 8px 16px;
                border-radius: 100px;
                font-size: 14px;
                color: #8aa9ff;
            }
            .subtitle {
                color: #9aa8c7;
                margin-bottom: 32px;
                font-size: 15px;
                border-left: 3px solid #3f5e9c;
                padding-left: 16px;
            }
            .input-group {
                margin-bottom: 24px;
            }
            label {
                display: block;
                color: #cbd5f0;
                margin-bottom: 8px;
                font-size: 14px;
            }
            input {
                width: 100%;
                padding: 16px 20px;
                background: #101827;
                border: 1px solid #2a3650;
                border-radius: 20px;
                color: white;
                font-size: 16px;
                transition: all 0.3s;
                outline: none;
            }
            input:focus {
                border-color: #5a8ce7;
                box-shadow: 0 0 0 4px rgba(90, 140, 231, 0.2);
            }
            button {
                width: 100%;
                padding: 16px;
                background: linear-gradient(95deg, #2d4b9e, #5a8ce7);
                border: none;
                border-radius: 24px;
                color: white;
                font-size: 18px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s;
                margin-top: 12px;
                box-shadow: 0 8px 20px rgba(45, 75, 158, 0.4);
            }
            button:hover {
                transform: translateY(-3px);
                box-shadow: 0 15px 30px rgba(90, 140, 231, 0.5);
            }
            .footer {
                text-align: center;
                margin-top: 28px;
                color: #7f8fb2;
            }
            .footer a {
                color: #8aa9ff;
                text-decoration: none;
                font-weight: 500;
                margin-left: 6px;
            }
        </style>
    </head>
    <body>
        <div class="card">
            <h2>📝 Kayıt Ol <span>yeni hesap</span></h2>
            <div class="subtitle">Akıllı notlarınızı oluşturun, hatırlatıcı ekleyin.</div>
            <form method="POST">
                <div class="input-group">
                    <label>Kullanıcı adı</label>
                    <input type="text" name="username" placeholder="örnek: ahmet_yilmaz" required>
                </div>
                <div class="input-group">
                    <label>Şifre</label>
                    <input type="password" name="password" placeholder="••••••••" required>
                </div>
                <button type="submit">Kayıt Ol</button>
            </form>
            <div class="footer">
                Zaten hesabın var mı? <a href="/login">Giriş Yap</a>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            return "Kullanıcı adı ve şifre gerekli."
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('notes'))
        return "Geçersiz giriş bilgileri."
    # Giriş formu
    return '''
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <title>Giriş - Akıllı Notlar</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
            body {
                background: linear-gradient(145deg, #0b1120 0%, #19233c 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }
            .card {
                background: rgba(18, 25, 40, 0.9);
                backdrop-filter: blur(10px);
                border-radius: 32px;
                padding: 48px 40px;
                width: 100%;
                max-width: 440px;
                box-shadow: 0 30px 60px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(90, 140, 231, 0.2);
                animation: float 0.8s ease-out;
            }
            @keyframes float {
                0% { opacity: 0; transform: translateY(30px); }
                100% { opacity: 1; transform: translateY(0); }
            }
            h2 {
                color: white;
                font-size: 32px;
                font-weight: 500;
                margin-bottom: 8px;
                display: flex;
                align-items: center;
                gap: 12px;
            }
            h2 span {
                background: #2d3d5c;
                padding: 8px 16px;
                border-radius: 100px;
                font-size: 14px;
                color: #8aa9ff;
            }
            .subtitle {
                color: #9aa8c7;
                margin-bottom: 32px;
                font-size: 15px;
                border-left: 3px solid #3f5e9c;
                padding-left: 16px;
            }
            .input-group {
                margin-bottom: 24px;
            }
            label {
                display: block;
                color: #cbd5f0;
                margin-bottom: 8px;
                font-size: 14px;
            }
            input {
                width: 100%;
                padding: 16px 20px;
                background: #101827;
                border: 1px solid #2a3650;
                border-radius: 20px;
                color: white;
                font-size: 16px;
                transition: all 0.3s;
                outline: none;
            }
            input:focus {
                border-color: #5a8ce7;
                box-shadow: 0 0 0 4px rgba(90, 140, 231, 0.2);
            }
            button {
                width: 100%;
                padding: 16px;
                background: linear-gradient(95deg, #2d4b9e, #5a8ce7);
                border: none;
                border-radius: 24px;
                color: white;
                font-size: 18px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s;
                margin-top: 12px;
                box-shadow: 0 8px 20px rgba(45, 75, 158, 0.4);
            }
            button:hover {
                transform: translateY(-3px);
                box-shadow: 0 15px 30px rgba(90, 140, 231, 0.5);
            }
            .footer {
                text-align: center;
                margin-top: 28px;
                color: #7f8fb2;
            }
            .footer a {
                color: #8aa9ff;
                text-decoration: none;
                font-weight: 500;
                margin-left: 6px;
            }
        </style>
    </head>
    <body>
        <div class="card">
            <h2>🔐 Giriş Yap <span>hoş geldin</span></h2>
            <div class="subtitle">Hatırlatıcılarını ve notlarını görüntüle.</div>
            <form method="POST">
                <div class="input-group">
                    <label>Kullanıcı adı</label>
                    <input type="text" name="username" placeholder="örnek: ahmet_yilmaz" required>
                </div>
                <div class="input-group">
                    <label>Şifre</label>
                    <input type="password" name="password" placeholder="••••••••" required>
                </div>
                <button type="submit">Giriş Yap</button>
            </form>
            <div class="footer">
                Hesabın yok mu? <a href="/register">Kayıt Ol</a>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/notes')
def notes():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM notes WHERE user_id = %s ORDER BY reminder_time NULLS LAST, created_at DESC", (user_id,))
    notes = cur.fetchall()
    cur.close()
    conn.close()
    
    # Şu anki UTC zamanı
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    
    notes_html = ''
    for note in notes:
        reminder_str = note['reminder_time'].strftime('%d.%m.%Y %H:%M') if note['reminder_time'] else 'Hatırlatma yok'
        status = ''
        if note['reminder_time']:
            if note['is_notified']:
                status = '<span style="color:#4CAF50;">✅ Bildirildi</span>'
            elif note['reminder_time'] <= now_utc:
                status = '<span style="color:#ff9800;">⏰ Zamanı geldi</span>'
            else:
                delta = note['reminder_time'] - now_utc
                minutes = int(delta.total_seconds() / 60)
                if minutes < 60:
                    status = f'<span style="color:#5a8ce7;">⏳ {minutes} dk</span>'
                else:
                    hours = minutes // 60
                    status = f'<span style="color:#5a8ce7;">⏳ {hours} sa</span>'
        notes_html += f'''
        <div class="note-card">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <h3>{note['title']}</h3>
                {status}
            </div>
            <p>{note['content']}</p>
            <div class="note-footer">
                <span>⏰ {reminder_str} UTC</span>
                <a href="/delete_note/{note['id']}" class="delete-btn" onclick="return confirm('Silmek istediğine emin misin?')">🗑️ Sil</a>
            </div>
        </div>
        '''
    
    return f'''
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <title>Notlarım - Akıllı Notlar</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', sans-serif; }}
            body {{
                background: #0b1120;
                color: #e0e0e0;
                padding: 24px;
            }}
            .navbar {{
                background: #141d2f;
                border-radius: 28px;
                padding: 16px 28px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 32px;
                border: 1px solid #28344e;
                box-shadow: 0 10px 20px rgba(0,0,0,0.5);
            }}
            .navbar h1 {{
                color: #8aa9ff;
                font-weight: 400;
                font-size: 24px;
            }}
            .navbar div {{
                display: flex;
                align-items: center;
                gap: 20px;
            }}
            .navbar a {{
                color: #b5c9ff;
                text-decoration: none;
                padding: 8px 20px;
                background: #1f2b40;
                border-radius: 40px;
                transition: 0.2s;
            }}
            .navbar a:hover {{
                background: #2e3f60;
            }}
            .container {{
                max-width: 900px;
                margin: 0 auto;
            }}
            .add-note {{
                background: #141d2f;
                border-radius: 28px;
                padding: 28px;
                margin-bottom: 32px;
                border: 1px solid #28344e;
            }}
            .add-note h2 {{
                color: #8aa9ff;
                margin-bottom: 20px;
                font-weight: 400;
            }}
            input, textarea {{
                width: 100%;
                padding: 14px 18px;
                margin: 10px 0;
                background: #0e1525;
                border: 1px solid #2a3a5a;
                border-radius: 18px;
                color: white;
                font-size: 16px;
            }}
            input:focus, textarea:focus {{
                border-color: #5a8ce7;
                outline: none;
                box-shadow: 0 0 0 3px rgba(90,140,231,0.2);
            }}
            textarea {{
                min-height: 100px;
                resize: vertical;
            }}
            .button-group {{
                display: flex;
                gap: 12px;
                flex-wrap: wrap;
                margin-top: 15px;
            }}
            button, .test-btn {{
                background: #1f2b40;
                border: none;
                color: white;
                padding: 12px 24px;
                border-radius: 40px;
                font-size: 15px;
                cursor: pointer;
                transition: 0.2s;
                border: 1px solid #3d506e;
            }}
            button:hover, .test-btn:hover {{
                background: #2d3f60;
                transform: translateY(-2px);
            }}
            .primary-btn {{
                background: linear-gradient(95deg, #2d4b9e, #5a8ce7);
                border: none;
                font-weight: 600;
            }}
            .notes-list {{
                display: flex;
                flex-direction: column;
                gap: 20px;
            }}
            .note-card {{
                background: #141d2f;
                border-radius: 24px;
                padding: 24px;
                border-left: 6px solid #5a8ce7;
                border: 1px solid #28344e;
                transition: 0.2s;
            }}
            .note-card:hover {{
                transform: translateY(-3px);
                box-shadow: 0 12px 24px rgba(0,0,0,0.6);
            }}
            .note-card h3 {{
                color: #8aa9ff;
                margin-bottom: 10px;
            }}
            .note-card p {{
                color: #b0c0e0;
                margin-bottom: 15px;
                white-space: pre-wrap;
            }}
            .note-footer {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-top: 1px solid #28344e;
                padding-top: 15px;
                color: #a0b0d0;
            }}
            .delete-btn {{
                color: #ff8a8a;
                text-decoration: none;
                padding: 5px 15px;
                background: #1f2b40;
                border-radius: 40px;
            }}
            .delete-btn:hover {{
                background: #3f2b40;
            }}
            .info {{
                text-align: center;
                color: #7f8fb2;
                padding: 40px;
                background: #141d2f;
                border-radius: 28px;
            }}
            .test-notification {{
                margin-top: 20px;
                padding: 15px;
                background: #0e1a2a;
                border-radius: 20px;
                border: 1px dashed #5a8ce7;
            }}
        </style>
    </head>
    <body>
        <div class="navbar">
            <h1>📒 Akıllı Notlar</h1>
            <div>
                <span>👤 {session['username']}</span>
                <a href="/logout">Çıkış</a>
            </div>
        </div>
        <div class="container">
            <div class="add-note">
                <h2>➕ Yeni Not</h2>
                <form action="/add_note" method="POST">
                    <input type="text" name="title" placeholder="Başlık" required>
                    <textarea name="content" placeholder="Not içeriği..." required></textarea>
                    <input type="datetime-local" name="reminder_time" id="reminderTime">
                    <div class="button-group">
                        <button type="submit" class="primary-btn">📌 Kaydet</button>
                        <button type="button" class="test-btn" onclick="setReminder(1)">⏰ +1 dk</button>
                        <button type="button" class="test-btn" onclick="setReminder(5)">⏰ +5 dk</button>
                    </div>
                </form>
                <!-- Test bildirimi butonu -->
                <div class="test-notification">
                    <strong>🔔 Test Bildirimi</strong>
                    <p style="margin: 10px 0; font-size:14px;">Anında bildirim gönderir (veritabanı kaydı olmadan).</p>
                    <button class="test-btn" onclick="testNotification()">📨 Test Bildirimi Gönder</button>
                </div>
            </div>

            <div class="notes-list">
                {notes_html if notes else '<div class="info">Henüz not eklemediniz. Yukarıdan ekleyebilirsiniz.</div>'}
            </div>
        </div>

        <script>
        function setReminder(minutes) {{
            const now = new Date();
            now.setMinutes(now.getMinutes() + minutes);
            const year = now.getFullYear();
            const month = String(now.getMonth() + 1).padStart(2, '0');
            const day = String(now.getDate()).padStart(2, '0');
            const hours = String(now.getHours()).padStart(2, '0');
            const mins = String(now.getMinutes()).padStart(2, '0');
            document.getElementById('reminderTime').value = `${{year}}-${{month}}-${{day}}T${{hours}}:${{mins}}`;
        }}

        function testNotification() {{
            fetch('/test_notify', {{ method: 'POST' }})
                .then(res => res.json())
                .then(data => alert(data.message))
                .catch(err => alert('Hata: ' + err));
        }}
        </script>
    </body>
    </html>
    '''

@app.route('/add_note', methods=['POST'])
def add_note():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    title = request.form['title']
    content = request.form['content']
    reminder_str = request.form.get('reminder_time')
    reminder_time = None
    if reminder_str:
        try:
            # Gelen string yerel saat olarak kabul edilir
            local_dt = datetime.fromisoformat(reminder_str)
            # UTC'ye çevir
            reminder_time = local_to_utc(local_dt)
            print(f"📅 Yerel: {local_dt} -> UTC: {reminder_time}")
        except Exception as e:
            print(f"❌ Tarih dönüşüm hatası: {e}")
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO notes (user_id, title, content, reminder_time, is_notified)
        VALUES (%s, %s, %s, %s, %s)
    """, (user_id, title, content, reminder_time, False))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('notes'))

@app.route('/delete_note/<int:note_id>')
def delete_note(note_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM notes WHERE id = %s AND user_id = %s", (note_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('notes'))

@app.route('/test_notify', methods=['POST'])
def test_notify():
    """Test bildirimi (veritabanı kullanmadan anında bildirim)"""
    if 'user_id' not in session:
        return jsonify({'message': 'Önce giriş yap'}), 403
    username = session['username']
    if PLYER_AVAILABLE:
        try:
            notification.notify(
                title="🧪 Test Bildirimi",
                message=f"Merhaba {username}, bu bir test bildirimidir!",
                timeout=3
            )
            return jsonify({'message': 'Bildirim gönderildi!'})
        except Exception as e:
            return jsonify({'message': f'Bildirim hatası: {e}'}), 500
    else:
        print(f"\n[TEST BİLDİRİMİ] {username} için test bildirimi")
        return jsonify({'message': 'Bildirim konsola yazdırıldı (plyer yok).'})

if __name__ == '__main__':
    init_db()
    print("🚀 Uygulama başlatılıyor... http://127.0.0.1:5000")
    app.run(debug=True, host='127.0.0.1', port=5000)
