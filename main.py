import os
import secrets
import string
import psycopg2
import requests
import base64
import io
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
CORS(app)
app.json.ensure_ascii = False

ADMIN_SECRET_PASSWORD = "2573486195715653N"

# ✅ متغير عام للتخزين المؤقت (Caching)
cached_schools = []

# دالة ذكية لتوزيع الأرقام على 5 قواعد (رقمين لكل قاعدة)
def get_db_index(school_id):
    if not school_id or not school_id[0].isdigit():
        return 0
    first_digit = int(school_id[0])
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
            print(f"❌ Missing credentials for DB_{db_index}")
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
        CREATE TABLE IF NOT EXISTS admin_keys (
            id SERIAL PRIMARY KEY,
            key_value VARCHAR(20) NOT NULL UNIQUE,
            is_used SMALLINT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

# ✅ دالة لتهيئة جميع القواعد عند بدء التشغيل
def init_all_databases():
    print("🚀 Initializing all 5 databases...")
    for i in range(5):
        conn = get_db_connection(str(i * 2))
        if conn:
            try:
                with conn.cursor() as cursor:
                    create_tables_if_not_exist(cursor)
                    conn.commit()
                    print(f"✅ DB_{i} tables created successfully.")
            except Exception as e:
                print(f"❌ Error initializing DB_{i}: {e}")
            finally:
                conn.close()
        else:
            print(f"⚠️ Could not connect to DB_{i} for initialization.")

# ✅ دالة لتحديث الذاكرة المؤقتة
def refresh_cache():
    global cached_schools
    print("🔄 Refreshing cache...")
    all_schools = []
    for i in range(5):
        connection = get_db_connection(str(i * 2)) 
        if not connection:
            continue
        try:
            with connection.cursor() as cursor:
                create_tables_if_not_exist(cursor)
                cursor.execute("""
                    SELECT s.school_id, s.school_name, si.image_url 
                    FROM schools s 
                    LEFT JOIN school_images si ON s.id = si.school_id 
                    ORDER BY s.id DESC
                """)
                all_schools.extend(cursor.fetchall())
        except Exception as e:
            print(f"❌ Error reading from DB_{i}: {e}")
        finally:
            if connection: connection.close()
            
    all_schools.sort(key=lambda x: x['school_id'], reverse=True)
    cached_schools = all_schools
    print("✅ Cache refreshed successfully.")

# استدعاء التهيئة عند بدء التشغيل
init_all_databases()
refresh_cache()

@app.route('/verify', methods=['POST'])
def verify_school():
    school_id = request.values.get('school_id')
    if not school_id:
        return jsonify({"status": "error", "school_name": None, "message": "الرجاء إدخال إيدي المدرسة"}), 400
    
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "school_name": None, "message": "خطأ في الاتصال بقاعدة البيانات"}), 500
    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            cursor.execute("SELECT school_name FROM schools WHERE school_id = %s", (school_id,))
            result = cursor.fetchone()
            if result:
                return jsonify({"status": "success", "school_name": result['school_name'], "message": None}), 200
            return jsonify({"status": "error", "school_name": None, "message": "الإيدي غير صحيح"}), 200
    except Exception as e:
        return jsonify({"status": "error", "school_name": None, "message": "خطأ داخلي في السيرفر"}), 500
    finally:
        if connection: connection.close()

@app.route('/verify_admin', methods=['POST'])
def verify_admin():
    password = request.values.get('password')
    if password == ADMIN_SECRET_PASSWORD:
        return jsonify({"status": "success", "message": "تم التحقق"}), 200
    else:
        return jsonify({"status": "error", "message": "كلمة السر غير صحيحة"}), 401

@app.route('/generate_key', methods=['GET'])
def generate_key():
    alphabet = string.ascii_uppercase + string.digits
    key = ''.join(secrets.choice(alphabet) for _ in range(12))
    
    connection = get_db_connection("0")
    if not connection:
        return jsonify({"status": "error", "message": "السيرفر لا يستطيع الاتصال بقاعدة البيانات"}), 500
        
    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            while True:
                cursor.execute("SELECT id FROM admin_keys WHERE key_value = %s", (key,))
                if not cursor.fetchone():
                    break
                key = ''.join(secrets.choice(alphabet) for _ in range(12))

            cursor.execute("INSERT INTO admin_keys (key_value) VALUES (%s)", (key,))
            connection.commit()
        return jsonify({"status": "success", "key": key}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"خطأ في السيرفر: {str(e)}"}), 500
    finally:
        if connection: connection.close()

@app.route('/create_school', methods=['POST'])
def create_school():
    school_name = request.values.get('school_name')
    admin_password = request.values.get('admin_password')
    image_base64 = request.values.get('image_base64', '')
    
    if not school_name or not admin_password:
        return jsonify({"status": "error", "message": "الرجاء إدخال الاسم وكلمة المرور"}), 400

    # البحث عن إيدي متاح في جميع القواعد
    school_id = None
    for _ in range(20):
        temp_id = ''.join(secrets.choice(string.digits) for _ in range(6))
        conn = get_db_connection(temp_id)
        if not conn: 
            continue
        try:
            with conn.cursor() as cursor:
                create_tables_if_not_exist(cursor) # تأكيد وجود الجداول
                cursor.execute("SELECT id FROM schools WHERE school_id = %s", (temp_id,))
                if not cursor.fetchone():
                    school_id = temp_id
                    break
        finally:
            if conn: conn.close()

    if not school_id:
        return jsonify({"status": "error", "message": "فشل في توليد إيدي، حاول مرة أخرى"}), 500

    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال بقاعدة البيانات"}), 500

    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor) # تأكيد إضافي
            
            cursor.execute("SELECT id FROM admin_keys WHERE key_value = %s AND is_used = 0", (admin_password.strip(),))
            key_row = cursor.fetchone()
            
            if not key_row:
                return jsonify({"status": "error", "message": "كلمة السر غير صحيحة أو تم استخدامها مسبقاً!"}), 400

            # رفع الصورة
            image_url = ""
            if image_base64:
                try:
                    if "," in image_base64:
                        image_base64 = image_base64.split(",")[1]
                    image_bytes = base64.b64decode(image_base64)
                    files = {'fileToUpload': ('school_image.jpg', image_bytes, 'image/jpeg')}
                    data = {'reqtype': 'fileupload'}
                    response = requests.post("https://catbox.moe/user/api.php", files=files, data=data)
                    if response.status_code == 200 and response.text.startswith("https://"):
                        image_url = response.text.strip()
                except Exception as e:
                    print(f"❌ Exception while uploading: {e}")

            # إدخال المدرسة
            cursor.execute("INSERT INTO schools (school_id, school_name) VALUES (%s, %s) RETURNING id", (school_id, school_name))
            new_school_db_id = cursor.fetchone()['id']

            if image_url:
                cursor.execute("INSERT INTO school_images (school_id, image_url) VALUES (%s, %s)", (new_school_db_id, image_url))
            
            cursor.execute("DELETE FROM admin_keys WHERE id = %s", (key_row['id'],))
            connection.commit()

        # ✅ تحديث الذاكرة المؤقتة بعد الإضافة
        refresh_cache()
        
        return jsonify({"status": "success", "message": f"تم إنشاء المدرسة بنجاح! الإيدي: {school_id}"}), 200
    except Exception as e:
        print(f"❌ Error in create_school: {e}")
        return jsonify({"status": "error", "message": f"خطأ في السيرفر: {str(e)}"}), 500
    finally:
        if connection: connection.close()

@app.route('/get_schools', methods=['GET'])
def get_schools():
    # ✅ إرجاع البيانات من الذاكرة المؤقتة فوراً
    return jsonify({"status": "success", "schools": cached_schools}), 200

@app.route('/update_school_id', methods=['POST'])
def update_school_id():
    old_id = request.values.get('old_id')
    new_id = request.values.get('new_id')
    if not old_id or not new_id or not new_id.isdigit() or len(new_id) != 6:
        return jsonify({"status": "error", "message": "بيانات غير صحيحة"}), 400
    
    if get_db_index(old_id) != get_db_index(new_id):
        return jsonify({"status": "error", "message": "لا يمكن نقل المدرسة لقاعدة بيانات أخرى! اختر إيدي ينتمي لنفس القاعدة."}), 400

    connection = get_db_connection(old_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            cursor.execute("SELECT id FROM schools WHERE school_id = %s", (new_id,))
            if cursor.fetchone():
                return jsonify({"status": "error", "message": "الإيدي مستخدم مسبقاً"}), 400
            cursor.execute("UPDATE schools SET school_id = %s WHERE school_id = %s", (new_id, old_id))
            connection.commit()
            # ✅ تحديث الذاكرة المؤقتة بعد التحديث
            refresh_cache()
            return jsonify({"status": "success", "message": "تم التحديث بنجاح"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": "خطأ في السيرفر"}), 500
    finally:
        if connection: connection.close()

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "School API is running successfully on 5 Databases!"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
