from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
from PIL import Image, ImageDraw, ImageFont
import sqlite3, os, json, io, base64

app = Flask(__name__)
app.secret_key = 'bk-tracker-2026-prod-key'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
ALLOWED_EXTENSIONS = {'png','jpg','jpeg','gif','webp'}
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('instance', exist_ok=True)

DB = 'instance/tracker.db'

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS coach (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT DEFAULT 'BK'
        );
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT UNIQUE NOT NULL,
            birthday TEXT NOT NULL,
            height REAL NOT NULL DEFAULT 160,
            gender TEXT DEFAULT 'M',
            password TEXT NOT NULL,
            goal_weight REAL DEFAULT 0,
            goal_date TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            weight REAL DEFAULT 0,
            body_fat REAL DEFAULT 0,
            visceral_fat REAL DEFAULT 0,
            muscle_mass REAL DEFAULT 0,
            bmr REAL DEFAULT 0,
            body_age INTEGER DEFAULT 0,
            waist REAL DEFAULT 0,
            abdomen REAL DEFAULT 0,
            hip REAL DEFAULT 0,
            thigh REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (client_id) REFERENCES clients(id)
        );
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            date TEXT NOT NULL,
            angle TEXT DEFAULT 'front',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (client_id) REFERENCES clients(id)
        );
    ''')
    coach = conn.execute("SELECT id FROM coach WHERE username='bk'").fetchone()
    if not coach:
        conn.execute("INSERT INTO coach (username, password, name) VALUES (?, ?, ?)",
                     ['bk', generate_password_hash('bkadmin2026'), 'BK'])
    conn.commit()
    conn.close()

init_db()

def allowed_file(f):
    return '.' in f and f.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS

def require_coach():
    if 'coach_id' not in session: return False
    return True

def require_client():
    if 'client_id' not in session: return False
    return True

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
        conn = get_db()
        c = conn.execute("SELECT * FROM coach WHERE username=?",[u]).fetchone()
        conn.close()
        if c and check_password_hash(c['password'],p):
            session['coach_id']=c['id']; session['coach_name']=c['name']; session.permanent=True
            return redirect(url_for('coach_dash'))
        return render_template('login.html',error='Invalid')
    return render_template('login.html')

@app.route('/client-login', methods=['GET','POST'])
def client_login():
    if request.method == 'POST':
        phone = request.form.get('phone','')
        pw = request.form.get('password','')
        conn = get_db()
        c = conn.execute("SELECT * FROM clients WHERE phone=? AND is_active=1",[phone]).fetchone()
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
    clients = conn.execute("""
        SELECT c.*,
            (SELECT date FROM sessions WHERE client_id=c.id ORDER BY date DESC LIMIT 1) as last_date,
            (SELECT weight FROM sessions WHERE client_id=c.id ORDER BY date DESC LIMIT 1) as last_weight,
            (SELECT weight FROM sessions WHERE client_id=c.id ORDER BY date DESC LIMIT 1 OFFSET 1) as prev_weight,
            (SELECT COUNT(*) FROM sessions WHERE client_id=c.id) as session_count
        FROM clients c WHERE c.is_active=1
        ORDER BY 
            CASE WHEN (SELECT date FROM sessions WHERE client_id=c.id ORDER BY date DESC LIMIT 1) IS NULL THEN 1 ELSE 0 END,
            (SELECT date FROM sessions WHERE client_id=c.id ORDER BY date DESC LIMIT 1) ASC
    """).fetchall()
    conn.close()
    
    client_data = []
    for c in clients:
        days = days_ago(c['last_date'])
        if days is None:
            status='new'; label='🆕 New'; color='#8a8a9a'
        elif days <= 10:
            status='ok'; label=f'✅ {days}d'; color='#4ec97e'
        elif days <= 14:
            status='soon'; label=f'⏰ Due in {14-days}d'; color='#f0d07a'
        elif days <= 18:
            status='warn'; label=f'⚠️ {days-14}d overdue'; color='#ff8c00'
        else:
            status='overdue'; label=f'🔴 {days-14}d overdue'; color='#ff6b6b'
        client_data.append({
            'id':c['id'],'name':c['name'],'phone':c['phone'],
            'height':c['height'],'gender':c['gender'],
            'goal_weight':c['goal_weight'],'last_weight':c['last_weight'],
            'prev_weight':c['prev_weight'],'last_date':c['last_date'],
            'session_count':c['session_count'],'days':days,
            'status':status,'label':label,'color':color
        })
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
            conn.execute("INSERT INTO clients (name,phone,birthday,height,gender,password,goal_weight) VALUES (?,?,?,?,?,?,?)",
                         [name,phone,bday,height,gender,generate_password_hash(pw),goal_w])
            conn.commit()
            conn.close()
            return redirect(url_for('coach_dash'))
        except sqlite3.IntegrityError:
            conn.close()
            return render_template('add_client.html',error='Phone already exists')
    return render_template('add_client.html')

# ===== CLIENT DETAIL =====
@app.route('/coach/client/<int:cid>')
def client_detail(cid):
    if not require_coach(): return redirect(url_for('login'))
    conn = get_db()
    client = conn.execute("SELECT * FROM clients WHERE id=?",[cid]).fetchone()
    if not client: conn.close(); return redirect(url_for('coach_dash'))
    sessions = conn.execute("SELECT * FROM sessions WHERE client_id=? ORDER BY date DESC LIMIT 50",[cid]).fetchall()
    photos = conn.execute("SELECT * FROM photos WHERE client_id=? ORDER BY date DESC",[cid]).fetchall()
    conn.close()
    return render_template('client_detail.html',client=client,sessions=sessions,photos=photos)

# ===== ADD SESSION =====
@app.route('/coach/session/<int:cid>', methods=['POST'])
def add_session(cid):
    if not require_coach(): return redirect(url_for('login'))
    d = request.form
    conn = get_db()
    def _f(v, default=0.0):
        try: return float(v) if v else default
        except: return default
    conn.execute("""INSERT INTO sessions 
        (client_id,date,weight,body_fat,visceral_fat,muscle_mass,bmr,body_age,waist,abdomen,hip,thigh,notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [cid, d.get('date',date.today().isoformat()),
         _f(d.get('weight')), _f(d.get('body_fat')), _f(d.get('visceral_fat')),
         _f(d.get('muscle_mass')), _f(d.get('bmr')), _f(d.get('body_age')),
         _f(d.get('waist')), _f(d.get('abdomen')), _f(d.get('hip')), _f(d.get('thigh')), d.get('notes','')])
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
        
        # Date stamp on photo
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
            pass  # date stamp optional
        
        conn = get_db()
        conn.execute("INSERT INTO photos (client_id,filename,date,angle) VALUES (?,?,?,?)",
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
    client = conn.execute("SELECT * FROM clients WHERE id=?",[cid]).fetchone()
    if not client: conn.close(); session.clear(); return redirect(url_for('client_login'))
    sessions = conn.execute("SELECT * FROM sessions WHERE client_id=? ORDER BY date DESC LIMIT 100",[cid]).fetchall()
    photos = conn.execute("SELECT * FROM photos WHERE client_id=? ORDER BY date DESC",[cid]).fetchall()
    chart_rows = conn.execute("SELECT date,weight,body_fat,waist,hip FROM sessions WHERE client_id=? ORDER BY date ASC",[cid]).fetchall()
    conn.close()
    chart_json = json.dumps([dict(r) for r in chart_rows])
    return render_template('client.html',client=client,sessions=sessions,photos=photos,chart_json=chart_json)

# ===== API =====
@app.route('/api/chart/<int:cid>')
def api_chart(cid):
    auth = ('coach_id' in session) or ('client_id' in session and session['client_id']==cid)
    if not auth: return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    data = conn.execute("""
        SELECT date,weight,body_fat,visceral_fat,muscle_mass,bmr,body_age,
               waist,abdomen,hip,thigh
        FROM sessions WHERE client_id=? ORDER BY date ASC
    """,[cid]).fetchall()
    conn.close()
    return jsonify([dict(d) for d in data])

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'],filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0',port=8080,debug=False)
