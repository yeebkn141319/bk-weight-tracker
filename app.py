from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
import werkzeug
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
from PIL import Image, ImageDraw, ImageFont
import os, json, io, base64

app = Flask(__name__)
app.secret_key = 'bk-tracker-2026-prod-key'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
ALLOWED_EXTENSIONS = {'png','jpg','jpeg','gif','webp'}
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ===== DATABASE ABSTRACTION (SQLite local / PostgreSQL on Render) =====
_HARDCODED_DB = 'postgresql://weight_tracker_db_xrw7_user:zpaPQXZwd40tO2dIUGNtxwTCakvYJx43@dpg-d8dckcmk1jcs738s2lvg-a/weight_tracker_db_xrw7'
_DB_URL = os.environ.get('DATABASE_URL', '') or _HARDCODED_DB

PG = _DB_URL.strip() != ''

if PG:
    import psycopg2
    import psycopg2.extras
    if _DB_URL.startswith('postgres://'):
        _DB_URL = _DB_URL.replace('postgres://', 'postgresql://', 1)
    
    def get_db():
        conn = psycopg2.connect(_DB_URL)
        conn.autocommit = False
        return conn
    
    def _exec(conn, sql, params=None):
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql.replace('?', '%s'), params or ())
        return cur
    
    def _is_dup_err(e):
        return isinstance(e, psycopg2.errors.UniqueViolation)
else:
    import sqlite3
    os.makedirs('instance', exist_ok=True)
    
    def get_db():
        conn = sqlite3.connect('instance/tracker.db')
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    
    def _exec(conn, sql, params=None):
        return conn.execute(sql, params or ())
    
    def _is_dup_err(e):
        return isinstance(e, sqlite3.IntegrityError)

def init_db():
    conn = get_db()
    if PG:
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS coach (id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, name TEXT DEFAULT 'BK')")
        cur.execute("CREATE TABLE IF NOT EXISTS clients (id SERIAL PRIMARY KEY, name TEXT NOT NULL, phone TEXT UNIQUE NOT NULL, birthday TEXT NOT NULL, height REAL NOT NULL DEFAULT 160, gender TEXT DEFAULT 'M', password TEXT NOT NULL, goal_weight REAL DEFAULT 0, goal_date TEXT DEFAULT '', notes TEXT DEFAULT '', is_active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        cur.execute("CREATE TABLE IF NOT EXISTS sessions (id SERIAL PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES clients(id), date TEXT NOT NULL, weight REAL DEFAULT 0, body_fat REAL DEFAULT 0, visceral_fat REAL DEFAULT 0, muscle_mass REAL DEFAULT 0, bmr REAL DEFAULT 0, body_age INTEGER DEFAULT 0, waist REAL DEFAULT 0, abdomen REAL DEFAULT 0, hip REAL DEFAULT 0, thigh REAL DEFAULT 0, notes TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        cur.execute("CREATE TABLE IF NOT EXISTS photos (id SERIAL PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES clients(id), filename TEXT NOT NULL, date TEXT NOT NULL, angle TEXT DEFAULT 'front', created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        cur.execute("SELECT id FROM coach WHERE username='bk'")
        if not cur.fetchone():
            cur.execute("INSERT INTO coach (username, password, name) VALUES (%s, %s, %s)", ['bk', generate_password_hash('bkadmin2026'), 'BK'])
        conn.commit()
        cur.close()
    else:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS coach (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, name TEXT DEFAULT 'BK');
            CREATE TABLE IF NOT EXISTS clients (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT UNIQUE NOT NULL, birthday TEXT NOT NULL, height REAL NOT NULL DEFAULT 160, gender TEXT DEFAULT 'M', password TEXT NOT NULL, goal_weight REAL DEFAULT 0, goal_date TEXT DEFAULT '', notes TEXT DEFAULT '', is_active INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime('now','localtime')));
            CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER NOT NULL, date TEXT NOT NULL, weight REAL DEFAULT 0, body_fat REAL DEFAULT 0, visceral_fat REAL DEFAULT 0, muscle_mass REAL DEFAULT 0, bmr REAL DEFAULT 0, body_age INTEGER DEFAULT 0, waist REAL DEFAULT 0, abdomen REAL DEFAULT 0, hip REAL DEFAULT 0, thigh REAL DEFAULT 0, notes TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now','localtime')), FOREIGN KEY (client_id) REFERENCES clients(id));
            CREATE TABLE IF NOT EXISTS photos (id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER NOT NULL, filename TEXT NOT NULL, date TEXT NOT NULL, angle TEXT DEFAULT 'front', created_at TEXT DEFAULT (datetime('now','localtime')), FOREIGN KEY (client_id) REFERENCES clients(id));
        ''')
        coach = conn.execute("SELECT id FROM coach WHERE username='bk'").fetchone()
        if not coach:
            conn.execute("INSERT INTO coach (username, password, name) VALUES (?, ?, ?)", ['bk', generate_password_hash('bkadmin2026'), 'BK'])
        conn.commit()
    conn.close()

init_db()

def allowed_file(f):
    return '.' in f and f.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS

def require_coach():
    return 'coach_id' in session

def require_client():
    return 'client_id' in session

@app.context_processor
def inject_today():
    return {'today': date.today().isoformat()}

@app.template_filter('days_ago')
def days_ago(d):
    if not d: return None
    try: return (datetime.now() - datetime.strptime(d[:10],'%Y-%m-%d')).days
    except: return None

@app.template_filter('fmt_date')
def fmt_date(d):
    if not d: return '-'
    try: return datetime.strptime(d[:10],'%Y-%m-%d').strftime('%d %b %Y')
    except: return d[:10]

@app.template_filter('calc_bio_age')
def calc_bio_age(bday):
    if not bday: return 0
    try:
        b = datetime.strptime(bday,'%Y-%m-%d')
        return datetime.now().year - b.year - ((datetime.now().month,datetime.now().day) < (b.month,b.day))
    except: return 0

@app.template_filter('calc_bmi')
def calc_bmi(weight, height):
    if not weight or not height: return 0
    try: return round(weight / ((height/100)**2), 1)
    except: return 0

# ===== HOME =====
@app.route('/')
def index():
    if 'coach_id' in session: return redirect(url_for('coach_dash'))
    if 'client_id' in session: return redirect(url_for('client_view'))
    return render_template('login.html')

# ===== LOGIN =====
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username','')
        p = request.form.get('password','')
        try:
            conn = get_db()
            c = _exec(conn, "SELECT * FROM coach WHERE username=?", [u]).fetchone()
            conn.close()
            debug = f'User={u}, Found={c is not None}, WZ={werkzeug.__version__}'
            if c:
                pw_hash = c['password']
                debug += f', Hash={pw_hash[:25]}...'
                try:
                    match = check_password_hash(pw_hash, p)
                    debug += f', Match={match}'
                except Exception as e:
                    debug += f', CheckErr={e}'
                if match:
                    session['coach_id']=c['id']; session['coach_name']=c['name']; session.permanent=True
                    return redirect(url_for('coach_dash'))
            return render_template('login.html',error=f'Invalid ({debug})')
        except Exception as e:
            return render_template('login.html',error=f'Error: {e}')
    return render_template('login.html')

@app.route('/client-login', methods=['GET','POST'])
def client_login():
    if request.method == 'POST':
        phone = request.form.get('phone','')
        pw = request.form.get('password','')
        conn = get_db()
        c = _exec(conn, "SELECT * FROM clients WHERE phone=? AND is_active=1", [phone]).fetchone()
        conn.close()
        if c and check_password_hash(c['password'],pw):
            session['client_id']=c['id']; session['client_name']=c['name']; session.permanent=True
            return redirect(url_for('client_view'))
        return render_template('client_login.html',error='Invalid')
    return render_template('client_login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# ===== COACH DASHBOARD =====
@app.route('/coach')
def coach_dash():
    if not require_coach(): return redirect(url_for('login'))
    conn = get_db()
    rows = _exec(conn, """
        SELECT c.*,
            (SELECT date FROM sessions WHERE client_id=c.id ORDER BY date DESC LIMIT 1) as last_date,
            (SELECT weight FROM sessions WHERE client_id=c.id ORDER BY date DESC LIMIT 1) as last_weight,
            (SELECT weight FROM sessions WHERE client_id=c.id ORDER BY date DESC LIMIT 1 OFFSET 1) as prev_weight,
            (SELECT COUNT(*) FROM sessions WHERE client_id=c.id) as session_count
        FROM clients c WHERE c.is_active=1
        ORDER BY CASE WHEN (SELECT date FROM sessions WHERE client_id=c.id ORDER BY date DESC LIMIT 1) IS NULL THEN 1 ELSE 0 END,
                 (SELECT date FROM sessions WHERE client_id=c.id ORDER BY date DESC LIMIT 1) ASC
    """).fetchall()
    conn.close()
    
    client_data = []
    for c in rows:
        d = days_ago(c['last_date'])
        if d is None:
            status='new'; label='🆕 New'; color='#8a8a9a'
        elif d <= 10: status='ok'; label=f'✅ {d}d'; color='#4ec97e'
        elif d <= 14: status='soon'; label=f'⏰ Due in {14-d}d'; color='#f0d07a'
        elif d <= 18: status='warn'; label=f'⚠️ {d-14}d overdue'; color='#ff8c00'
        else: status='overdue'; label=f'🔴 {d-14}d overdue'; color='#ff6b6b'
        client_data.append({'id':c['id'],'name':c['name'],'phone':c['phone'],'height':c['height'],
            'gender':c['gender'],'goal_weight':c['goal_weight'],'last_weight':c['last_weight'],
            'prev_weight':c['prev_weight'],'last_date':c['last_date'],'session_count':c['session_count'],
            'days':d,'status':status,'label':label,'color':color})
    return render_template('coach.html',clients=client_data,name=session.get('coach_name',''))

# ===== ADD CLIENT =====
@app.route('/coach/add', methods=['GET','POST'])
def add_client():
    if not require_coach(): return redirect(url_for('login'))
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        phone = request.form.get('phone','').strip()
        bday = request.form.get('birthday','')
        height = float(request.form.get('height',160))
        gender = request.form.get('gender','M')
        pw = ''.join(filter(str.isdigit, bday.replace('-','')))[-6:] if bday else '010101'
        goal_w = float(request.form.get('goal_weight',0))
        if not name or not phone:
            return render_template('add_client.html',error='Name & phone required')
        conn = get_db()
        try:
            _exec(conn, "INSERT INTO clients (name,phone,birthday,height,gender,password,goal_weight) VALUES (?,?,?,?,?,?,?)",
                  [name,phone,bday,height,gender,generate_password_hash(pw),goal_w])
            conn.commit()
            conn.close()
            return redirect(url_for('coach_dash'))
        except Exception as e:
            conn.close()
            if _is_dup_err(e):
                return render_template('add_client.html',error='Phone already exists')
            return render_template('add_client.html',error='Error saving client')

# ===== CLIENT DETAIL =====
@app.route('/coach/client/<int:cid>')
def client_detail(cid):
    if not require_coach(): return redirect(url_for('login'))
    conn = get_db()
    client = _exec(conn, "SELECT * FROM clients WHERE id=?", [cid]).fetchone()
    if not client: conn.close(); return redirect(url_for('coach_dash'))
    sessions = _exec(conn, "SELECT * FROM sessions WHERE client_id=? ORDER BY date DESC LIMIT 50", [cid]).fetchall()
    photos = _exec(conn, "SELECT * FROM photos WHERE client_id=? ORDER BY date DESC", [cid]).fetchall()
    conn.close()
    return render_template('client_detail.html',client=client,sessions=sessions,photos=photos)

# ===== ADD SESSION =====
@app.route('/coach/session/<int:cid>', methods=['POST'])
def add_session(cid):
    if not require_coach(): return redirect(url_for('login'))
    d = request.form
    def _f(v, default=0.0):
        try: return float(v) if v else default
        except: return default
    conn = get_db()
    _exec(conn, "INSERT INTO sessions (client_id,date,weight,body_fat,visceral_fat,muscle_mass,bmr,body_age,waist,abdomen,hip,thigh,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
          [cid, d.get('date',date.today().isoformat()),
           _f(d.get('weight')), _f(d.get('body_fat')), _f(d.get('visceral_fat')),
           _f(d.get('muscle_mass')), _f(d.get('bmr')), _f(d.get('body_age')),
           _f(d.get('waist')), _f(d.get('abdomen')), _f(d.get('hip')), _f(d.get('thigh')), d.get('notes','')])
    conn.commit()
    conn.close()
    return redirect(url_for('client_detail',cid=cid))

# ===== DELETE SESSION =====
@app.route('/coach/session_delete/<int:sid>/<int:cid>', methods=['POST'])
def delete_session(sid, cid):
    if not require_coach(): return redirect(url_for('login'))
    conn = get_db()
    _exec(conn, "DELETE FROM sessions WHERE id=? AND client_id=?", [sid, cid])
    conn.commit()
    conn.close()
    return redirect(url_for('client_detail',cid=cid))

# ===== DELETE PHOTO =====
@app.route('/coach/photo_delete/<int:pid>/<int:cid>', methods=['POST'])
def delete_photo(pid, cid):
    if not require_coach(): return redirect(url_for('login'))
    conn = get_db()
    photo = _exec(conn, "SELECT filename FROM photos WHERE id=? AND client_id=?", [pid, cid]).fetchone()
    if photo:
        fpath = os.path.join(app.config['UPLOAD_FOLDER'], photo['filename'])
        if os.path.exists(fpath): os.remove(fpath)
        _exec(conn, "DELETE FROM photos WHERE id=?", [pid])
        conn.commit()
    conn.close()
    return redirect(url_for('client_detail',cid=cid))

# ===== UPLOAD PHOTO =====
@app.route('/coach/photo/<int:cid>', methods=['POST'])
def upload_photo(cid):
    if not require_coach(): return redirect(url_for('login'))
    if 'photo' not in request.files: return redirect(url_for('client_detail',cid=cid))
    file = request.files['photo']
    s_date = request.form.get('date',date.today().isoformat())
    if file and allowed_file(file.filename):
        ext = file.filename.rsplit('.',1)[1].lower()
        fname = f"c{cid}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
        path = os.path.join(app.config['UPLOAD_FOLDER'], fname)
        file.save(path)
        try:
            img = Image.open(path)
            draw = ImageDraw.Draw(img)
            dt_text = f"📅 {s_date}"
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", max(20, img.width//25))
            except:
                font = ImageFont.load_default()
            bbox = draw.textbbox((0,0), dt_text, font=font)
            tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
            x, y = 10, img.height - th - 15
            draw.rectangle([x-5, y-5, x+tw+10, y+th+10], fill=(0,0,0,180))
            draw.text((x+2, y+2), dt_text, fill=(255,255,255), font=font)
            img.save(path)
        except:
            pass
        conn = get_db()
        _exec(conn, "INSERT INTO photos (client_id,filename,date,angle) VALUES (?,?,?,?)",
              [cid, fname, s_date, request.form.get('angle','front')])
        conn.commit()
        conn.close()
    return redirect(url_for('client_detail',cid=cid))

# ===== CLIENT VIEW =====
@app.route('/client')
def client_view():
    if not require_client(): return redirect(url_for('client_login'))
    cid = session['client_id']
    conn = get_db()
    client = _exec(conn, "SELECT * FROM clients WHERE id=?", [cid]).fetchone()
    if not client: conn.close(); session.clear(); return redirect(url_for('client_login'))
    sessions = _exec(conn, "SELECT * FROM sessions WHERE client_id=? ORDER BY date DESC LIMIT 100", [cid]).fetchall()
    photos = _exec(conn, "SELECT * FROM photos WHERE client_id=? ORDER BY date DESC", [cid]).fetchall()
    chart_rows = _exec(conn, "SELECT date,weight,body_fat,waist,hip FROM sessions WHERE client_id=? ORDER BY date ASC", [cid]).fetchall()
    conn.close()
    chart_json = json.dumps([dict(r) for r in chart_rows])
    return render_template('client.html',client=client,sessions=sessions,photos=photos,chart_json=chart_json)

# ===== API =====
@app.route('/api/chart/<int:cid>')
def api_chart(cid):
    auth = ('coach_id' in session) or ('client_id' in session and session['client_id']==cid)
    if not auth: return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    data = _exec(conn, "SELECT date,weight,body_fat,visceral_fat,muscle_mass,bmr,body_age,waist,abdomen,hip,thigh FROM sessions WHERE client_id=? ORDER BY date ASC", [cid]).fetchall()
    conn.close()
    return jsonify([dict(d) for d in data])

@app.route('/_dbcheck')
def db_check():
    status = f'PG mode: {PG}\nDB_URL set: {bool(_DB_URL)}\nDB_URL prefix: {_DB_URL[:20]}...\n'
    try:
        conn = get_db()
        cur = _exec(conn, "SELECT username FROM coach")
        coaches = [r['username'] for r in cur.fetchall()]
        conn.close()
        status += f'Coaches: {coaches}\nDB OK'
    except Exception as e:
        status += f'DB Error: {e}'
    return f'<pre>{status}</pre>'

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'],filename)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
