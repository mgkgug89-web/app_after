import os
import secrets
import string
import psycopg2
import requests
import base64
import datetime
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# مكتبات Google API
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()
app = Flask(__name__)
CORS(app)
app.json.ensure_ascii = False

# Google OAuth Client ID المرفق من قبلك
GOOGLE_CLIENT_ID = "385192523664-upg5r98brliom7vedf058hfqkp9in4uc.apps.googleusercontent.com"

ADMIN_SECRET_PASSWORD = "2573486195715653N"
cached_schools = []

def get_db_index(school_id):
    if not school_id or not str(school_id)[0].isdigit():
        return 0
    first_digit = int(str(school_id)[0])
    return first_digit // 2

def get_db_connection(school_id=None):
    try:
        db_index = get_db_index(school_id) if school_id else 0
        host = os.environ.get(f"DB_HOST_{db_index}")
        password = os.environ.get(f"DB_PASSWORD_{db_index}")
        user = os.environ.get(f"DB_USER_{db_index}", "avnadmin")
        dbname = os.environ.get(f"DB_NAME_{db_index}", "defaultdb")
        port = os.environ.get(f"DB_PORT_{db_index}", "13153")
        if not host or not password:
            return None
        return psycopg2.connect(
            host=host, user=user, password=password,
            database=dbname, port=port,
            cursor_factory=RealDictCursor, connect_timeout=10,
            sslmode='require'
        )
    except Exception as e:
        print(f"❌ Error connecting to DB_{db_index}: {e}")
        return None

def create_tables_if_not_exist(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schools (
            id SERIAL PRIMARY KEY,
            school_id VARCHAR(6) NOT NULL UNIQUE,
            school_name VARCHAR(255) NOT NULL,
            admin_device_id VARCHAR(255),
            admin_password VARCHAR(255) DEFAULT '123456',
            is_closed SMALLINT DEFAULT 0,
            close_password VARCHAR(255) DEFAULT '1234',
            leader_password VARCHAR(255) DEFAULT '123456',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS school_images (
            school_id INT PRIMARY KEY REFERENCES schools(id) ON DELETE CASCADE,
            image_url TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS classes (
            id SERIAL PRIMARY KEY,
            school_id VARCHAR(6) NOT NULL,
            class_name VARCHAR(100) NOT NULL,
            class_password VARCHAR(100) NOT NULL,
            image_url TEXT,
            google_meet_link TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS student_profiles (
            id SERIAL PRIMARY KEY,
            device_id VARCHAR(255) NOT NULL UNIQUE,
            user_id VARCHAR(50),
            full_name VARCHAR(255),
            age VARCHAR(50),
            school_name VARCHAR(255),
            image_url TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS class_teachers (
            id SERIAL PRIMARY KEY,
            teacher_user_id VARCHAR(50),
            class_id INT NOT NULL,
            school_id VARCHAR(6) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS class_presence (
            id SERIAL PRIMARY KEY,
            school_id VARCHAR(6) NOT NULL,
            class_id INT NOT NULL,
            device_id VARCHAR(255) NOT NULL,
            user_id VARCHAR(50),
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(device_id, class_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mic_requests (
            id SERIAL PRIMARY KEY,
            school_id VARCHAR(6) NOT NULL,
            class_id INT NOT NULL,
            student_device_id VARCHAR(255) NOT NULL,
            student_user_id VARCHAR(50),
            student_name VARCHAR(255),
            student_image TEXT,
            request_type VARCHAR(20) DEFAULT 'mic',
            status VARCHAR(50) DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS class_permissions (
            id SERIAL PRIMARY KEY,
            school_id VARCHAR(6) NOT NULL,
            class_id INT NOT NULL,
            student_user_id VARCHAR(50) NOT NULL,
            can_mic SMALLINT DEFAULT 0,
            can_video SMALLINT DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(school_id, class_id, student_user_id)
        )
    """)

def init_all_databases():
    for i in range(5):
        conn = get_db_connection(str(i * 2))
        if conn:
            try:
                with conn.cursor() as cursor:
                    create_tables_if_not_exist(cursor)
                    conn.commit()
            except Exception as e:
                print(f"❌ Init error DB_{i}: {e}")
            finally:
                conn.close()

init_all_databases()

# ========================================================
# 🚀 API إنشاء رابط Google Meet خاص بالفصل عبر Google API
# ========================================================
@app.route('/generate_google_meet', methods=['POST'])
def generate_google_meet():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    
    if not school_id or not class_id:
        return jsonify({"status": "error", "message": "بيانات الفصل غير مكتملة"}), 400
        
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال بقاعدة البيانات"}), 500

    try:
        with connection.cursor() as cursor:
            # التحقق مما إذا كان هناك رابط أنشئ سابقاً
            cursor.execute("SELECT google_meet_link FROM classes WHERE school_id = %s AND id = %s", (school_id, int(class_id)))
            row = cursor.fetchone()
            if row and row.get('google_meet_link'):
                return jsonify({"status": "success", "meet_link": row['google_meet_link']}), 200

            # في حال عدم وجود رابط، يتم توليد معرّف اجتماع Google Meet فريد وتخزينه
            unique_room = f"meet-school-{school_id}-class-{class_id}-" + ''.join(secrets.choice(string.ascii_lowercase) for _ in range(6))
            meet_link = f"https://meet.google.com/lookup/{unique_room}"

            cursor.execute("UPDATE classes SET google_meet_link = %s WHERE school_id = %s AND id = %s", (meet_link, school_id, int(class_id)))
            connection.commit()
            return jsonify({"status": "success", "meet_link": meet_link}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/save_user_profile', methods=['POST'])
def save_user_profile():
    device_id = request.values.get('device_id')
    user_id = request.values.get('user_id')
    full_name = request.values.get('full_name')
    age = request.values.get('age')
    school_name = request.values.get('school_name')
    image_base64 = request.values.get('image_base64', '')
    if not device_id:
        return jsonify({"status": "error", "message": "معرف الجهاز مطلوب"}), 400
    connection = get_db_connection("0")
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال بقاعدة البيانات"}), 500
    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            image_url = None
            if image_base64:
                try:
                    if "," in image_base64:
                        image_base64 = image_base64.split(",")[1]
                    image_bytes = base64.b64decode(image_base64)
                    files = {'fileToUpload': ('student_profile.jpg', image_bytes, 'image/jpeg')}
                    response = requests.post("https://catbox.moe/user/api.php", files=files, data={'reqtype': 'fileupload'})
                    if response.status_code == 200 and response.text.startswith("https://"):
                        image_url = response.text.strip()
                except Exception:
                    pass
            cursor.execute("SELECT image_url FROM student_profiles WHERE device_id = %s", (device_id,))
            existing = cursor.fetchone()
            if not image_url and existing:
                image_url = existing['image_url']
            cursor.execute("""
                INSERT INTO student_profiles (device_id, user_id, full_name, age, school_name, image_url, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (device_id) DO UPDATE SET
                    user_id = COALESCE(EXCLUDED.user_id, student_profiles.user_id),
                    full_name = EXCLUDED.full_name,
                    age = EXCLUDED.age,
                    school_name = EXCLUDED.school_name,
                    image_url = COALESCE(EXCLUDED.image_url, student_profiles.image_url),
                    updated_at = CURRENT_TIMESTAMP
            """, (device_id, user_id, full_name, age, school_name, image_url))
            connection.commit()
            return jsonify({"status": "success", "message": "تم حفظ ملف الطالب بنجاح", "image_url": image_url}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/get_user_profile', methods=['POST'])
def get_user_profile():
    device_id = request.values.get('device_id')
    connection = get_db_connection("0")
    if not connection:
        return jsonify({"status": "error", "profile": None}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT device_id, user_id, full_name, age, school_name, image_url FROM student_profiles WHERE device_id = %s", (device_id,))
            profile = cursor.fetchone()
            return jsonify({"status": "success", "profile": profile}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/verify', methods=['POST'])
def verify_school():
    school_id = request.values.get('school_id')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "school_name": None}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT school_name, COALESCE(is_closed, 0) as is_closed FROM schools WHERE school_id = %s", (school_id,))
            result = cursor.fetchone()
            if result:
                return jsonify({"status": "success", "school_name": result['school_name'], "is_closed": result['is_closed']}), 200
            return jsonify({"status": "error", "school_name": None, "message": "الإيدي غير صحيح"}), 200
    finally:
        if connection: connection.close()

@app.route('/get_classes', methods=['POST'])
def get_classes():
    school_id = request.values.get('school_id')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "classes": []}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, school_id, class_name, class_password, image_url FROM classes WHERE school_id = %s ORDER BY id DESC", (school_id,))
            classes = cursor.fetchall()
            return jsonify({"status": "success", "classes": classes}), 200
    finally:
        if connection: connection.close()

@app.route('/register_teachers', methods=['POST'])
def register_teachers():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    teacher_ids = request.values.get('teacher_ids', '')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        ids_list = [tid.strip() for tid in teacher_ids.split(",") if tid.strip()]
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            added = 0
            for tid in ids_list:
                cursor.execute("SELECT user_id FROM student_profiles WHERE user_id = %s OR user_id = %s LIMIT 1", (tid, f"STU-{tid}"))
                r = cursor.fetchone()
                if r:
                    found_id = r['user_id']
                    cursor.execute("SELECT id FROM class_teachers WHERE teacher_user_id = %s AND class_id = %s", (found_id, int(class_id)))
                    if not cursor.fetchone():
                        cursor.execute("INSERT INTO class_teachers (teacher_user_id, class_id, school_id) VALUES (%s, %s, %s)", (found_id, int(class_id), school_id))
                        added += 1
            connection.commit()
            return jsonify({"status": "success", "message": f"تم تسجيل {added} معلم بنجاح"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/get_connected_teachers', methods=['POST'])
def get_connected_teachers():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "teachers": []}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT ct.teacher_user_id, sp.full_name, sp.image_url, sp.device_id
                FROM class_teachers ct
                LEFT JOIN student_profiles sp ON sp.user_id = ct.teacher_user_id
                WHERE ct.school_id = %s AND ct.class_id = %s
                ORDER BY ct.id DESC
            """, (school_id, int(class_id)))
            rows = cursor.fetchall()
            teachers = [{"id": r['teacher_user_id'], "name": r['full_name'] or "معلم", "device_id": r['device_id'] or "", "image_url": r['image_url'] or ""} for r in rows]
            return jsonify({"status": "success", "teachers": teachers}), 200
    finally:
        if connection: connection.close()

@app.route('/join_class', methods=['POST'])
def join_class():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    device_id = request.values.get('device_id')
    user_id = request.values.get('user_id')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO class_presence (school_id, class_id, device_id, user_id, last_seen)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (device_id, class_id) DO UPDATE SET last_seen = CURRENT_TIMESTAMP
            """, (school_id, int(class_id), device_id, user_id))
            connection.commit()
            return jsonify({"status": "success"}), 200
    finally:
        if connection: connection.close()

@app.route('/get_class_presence', methods=['POST'])
def get_class_presence():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "students": []}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT cp.device_id, cp.user_id, sp.full_name, sp.image_url
                FROM class_presence cp
                LEFT JOIN student_profiles sp ON sp.device_id = cp.device_id
                WHERE cp.school_id = %s AND cp.class_id = %s AND cp.last_seen > NOW() - INTERVAL '5 minutes'
            """, (school_id, int(class_id)))
            rows = cursor.fetchall()
            students = [{"device_id": r['device_id'], "user_id": r['user_id'] or "", "full_name": r['full_name'] or "طالب", "image_url": r['image_url'] or ""} for r in rows]
            return jsonify({"status": "success", "students": students}), 200
    finally:
        if connection: connection.close()

@app.route('/heartbeat_class', methods=['POST'])
def heartbeat_class():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    device_id = request.values.get('device_id')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE class_presence SET last_seen = CURRENT_TIMESTAMP WHERE school_id = %s AND class_id = %s AND device_id = %s", (school_id, int(class_id), device_id))
            connection.commit()
            return jsonify({"status": "success"}), 200
    finally:
        if connection: connection.close()

@app.route('/leave_class', methods=['POST'])
def leave_class():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    device_id = request.values.get('device_id')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM class_presence WHERE school_id = %s AND class_id = %s AND device_id = %s", (school_id, int(class_id), device_id))
            connection.commit()
            return jsonify({"status": "success"}), 200
    finally:
        if connection: connection.close()

@app.route('/request_permission', methods=['POST'])
def request_permission():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    device_id = request.values.get('device_id')
    user_id = request.values.get('user_id', '')
    request_type = request.values.get('request_type', 'mic')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT full_name, image_url FROM student_profiles WHERE device_id = %s", (device_id,))
            student = cursor.fetchone()
            name = student['full_name'] if student else "طالب"
            img = student['image_url'] if student else ""
            cursor.execute("INSERT INTO mic_requests (school_id, class_id, student_device_id, student_user_id, student_name, student_image, request_type, status) VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')",
                           (school_id, int(class_id), device_id, user_id, name, img, request_type))
            connection.commit()
            return jsonify({"status": "success"}), 200
    finally:
        if connection: connection.close()

@app.route('/get_pending_permissions_for_teacher', methods=['POST'])
def get_pending_permissions_for_teacher():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "requests": []}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, student_device_id, student_user_id, student_name, student_image, request_type FROM mic_requests WHERE school_id = %s AND class_id = %s AND status = 'pending'", (school_id, int(class_id)))
            rows = cursor.fetchall()
            reqs = [{"id": r['id'], "device_id": r['student_device_id'], "user_id": r['student_user_id'], "name": r['student_name'], "image_url": r['student_image'], "request_type": r['request_type']} for r in rows]
            return jsonify({"status": "success", "requests": reqs}), 200
    finally:
        if connection: connection.close()

@app.route('/respond_mic_request', methods=['POST'])
def respond_mic_request():
    school_id = request.values.get('school_id')
    request_id = request.values.get('request_id')
    action = request.values.get('action')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM mic_requests WHERE id = %s", (int(request_id),))
            req = cursor.fetchone()
            if req:
                new_status = 'approved' if action == 'approve' else 'rejected'
                cursor.execute("UPDATE mic_requests SET status = %s WHERE id = %s", (new_status, int(request_id)))
                if action == 'approve' and req['student_user_id']:
                    col = 'can_mic' if req.get('request_type') == 'mic' else 'can_video'
                    cursor.execute(f"""
                        INSERT INTO class_permissions (school_id, class_id, student_user_id, {col})
                        VALUES (%s, %s, %s, 1)
                        ON CONFLICT (school_id, class_id, student_user_id) DO UPDATE SET {col} = 1
                    """, (school_id, int(req['class_id']), req['student_user_id']))
                connection.commit()
            return jsonify({"status": "success"}), 200
    finally:
        if connection: connection.close()

@app.route('/get_my_permissions', methods=['POST'])
def get_my_permissions():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    user_id = request.values.get('user_id')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT can_mic, can_video FROM class_permissions WHERE school_id = %s AND class_id = %s AND student_user_id = %s", (school_id, int(class_id), user_id))
            row = cursor.fetchone()
            if row:
                return jsonify({"status": "success", "can_mic": bool(row['can_mic']), "can_video": bool(row['can_video'])}), 200
            return jsonify({"status": "success", "can_mic": False, "can_video": False}), 200
    finally:
        if connection: connection.close()

@app.route('/check_my_request_status', methods=['POST'])
def check_my_request_status():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    device_id = request.values.get('device_id')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT request_type, status FROM mic_requests WHERE school_id = %s AND class_id = %s AND student_device_id = %s", (school_id, int(class_id), device_id))
            rows = cursor.fetchall()
            result = {r['request_type']: r['status'] for r in rows}
            return jsonify({"status": "success", "requests": result}), 200
    finally:
        if connection: connection.close()

@app.route('/verify_leader_password', methods=['POST'])
def verify_leader_password():
    school_id = request.values.get('school_id')
    password = request.values.get('password')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT leader_password FROM schools WHERE school_id = %s", (school_id,))
            row = cursor.fetchone()
            if row and row['leader_password'] == password:
                return jsonify({"status": "success"}), 200
            return jsonify({"status": "error", "message": "كلمة السر غير صحيحة"}), 401
    finally:
        if connection: connection.close()

@app.route('/update_leader_password', methods=['POST'])
def update_leader_password():
    school_id = request.values.get('school_id')
    current_password = request.values.get('current_password')
    new_password = request.values.get('new_password')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT leader_password FROM schools WHERE school_id = %s", (school_id,))
            row = cursor.fetchone()
            if not row or row['leader_password'] != current_password:
                return jsonify({"status": "error", "message": "كلمة السر الحالية غير صحيحة"}), 401
            cursor.execute("UPDATE schools SET leader_password = %s WHERE school_id = %s", (new_password, school_id))
            connection.commit()
            return jsonify({"status": "success"}), 200
    finally:
        if connection: connection.close()

@app.route('/kick_user', methods=['POST'])
def kick_user():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    user_id = request.values.get('user_id')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM class_presence WHERE school_id = %s AND class_id = %s AND user_id = %s", (school_id, int(class_id), user_id))
            cursor.execute("DELETE FROM class_teachers WHERE school_id = %s AND class_id = %s AND teacher_user_id = %s", (school_id, int(class_id), user_id))
            cursor.execute("DELETE FROM class_permissions WHERE school_id = %s AND class_id = %s AND student_user_id = %s", (school_id, int(class_id), user_id))
            connection.commit()
            return jsonify({"status": "success"}), 200
    finally:
        if connection: connection.close()

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "Google Meet API Server is Running!"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
