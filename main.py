import os
import secrets
import string
import psycopg2
import requests
import base64
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
CORS(app)
app.json.ensure_ascii = False

# ========================================================
# ⚠️⚠️ إعدادات قاعدة البيانات و ImgBB ⚠️⚠️
# ========================================================
DB_HOST = os.environ.get("DB_HOST", "dpg-dbh4nsu1egvs739f4lhg-a")
DB_USER = os.environ.get("DB_USER", "school_db_49kb_user")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "VELcHkBNHgaL7pfUmCNTUFv9nw20ZbsC")
DB_NAME = os.environ.get("DB_NAME", "school_db_49kb")
DB_PORT = os.environ.get("DB_PORT", "5432")

ADMIN_SECRET_PASSWORD = "2573486195715653N"
IMGBB_API_KEY = "ef1c80e46e5d60d8c70ce4ddf3fcf500" # ✅ المفتاح الخاص بك

def get_db_connection():
    try:
        return psycopg2.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
            database=DB_NAME, port=DB_PORT,
            cursor_factory=RealDictCursor, connect_timeout=10,
            sslmode='require'
        )
    except Exception as e:
        print(f"❌ Error connecting to PostgreSQL: {e}")
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

@app.route('/verify', methods=['POST'])
def verify_school():
    school_id = request.values.get('school_id')
    if not school_id:
        return jsonify({"status": "error", "school_name": None, "message": "الرجاء إدخال إيدي المدرسة"}), 400
    
    connection = get_db_connection()
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
    
    connection = get_db_connection()
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
    image_base64 = request.values.get('image_base64', '') # ✅ نستقبل الصورة كـ Base64
    
    if not school_name or not admin_password:
        return jsonify({"status": "error", "message": "الرجاء إدخال الاسم وكلمة المرور"}), 400

    connection = get_db_connection()
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال بقاعدة البيانات"}), 500

    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            
            cursor.execute("SELECT id FROM admin_keys WHERE key_value = %s AND is_used = 0", (admin_password.strip(),))
            key_row = cursor.fetchone()
            
            if not key_row:
                return jsonify({"status": "error", "message": "كلمة السر غير صحيحة أو تم استخدامها مسبقاً!"}), 400

            # ✅ رفع الصورة إلى ImgBB (إذا كانت موجودة)
            image_url = ""
            if image_base64:
                try:
                    # ImgBB يحتاج الصورة كـ base64 بدون البادئة (data:image/jpeg;base64,)
                    if "," in image_base64:
                        image_base64 = image_base64.split(",")[1]
                    
                    payload = {
                        "key": IMGBB_API_KEY,
                        "image": image_base64
                    }
                    response = requests.post("https://api.imgbb.com/1/upload", data=payload)
                    res_data = response.json()
                    
                    if res_data.get('success'):
                        image_url = res_data['data']['url'] # ✅ الرابط المباشر
                        print(f"✅ Image uploaded: {image_url}")
                    else:
                        print(f"❌ ImgBB Error: {res_data}")
                except Exception as e:
                    print(f"❌ Error uploading to ImgBB: {e}")

            # توليد الإيدي وإدخال المدرسة
            while True:
                school_id = ''.join(secrets.choice(string.digits) for _ in range(6))
                cursor.execute("SELECT id FROM schools WHERE school_id = %s", (school_id,))
                if not cursor.fetchone():
                    break

            cursor.execute("INSERT INTO schools (school_id, school_name) VALUES (%s, %s) RETURNING id", (school_id, school_name))
            new_school_db_id = cursor.fetchone()['id']

            # حفظ رابط الصورة في الجدول
            if image_url:
                cursor.execute("INSERT INTO school_images (school_id, image_url) VALUES (%s, %s)", (new_school_db_id, image_url))
            
            cursor.execute("DELETE FROM admin_keys WHERE id = %s", (key_row['id'],))
            connection.commit()

        return jsonify({"status": "success", "message": f"تم إنشاء المدرسة بنجاح! الإيدي: {school_id}"}), 200
    except Exception as e:
        print(f"❌ Error in create_school: {e}")
        return jsonify({"status": "error", "message": f"خطأ في السيرفر: {str(e)}"}), 500
    finally:
        if connection: connection.close()

@app.route('/get_schools', methods=['GET'])
def get_schools():
    connection = get_db_connection()
    if not connection:
        return jsonify({"status": "error", "schools": []}), 500
    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            cursor.execute("""
                SELECT s.school_id, s.school_name, si.image_url 
                FROM schools s 
                LEFT JOIN school_images si ON s.id = si.school_id 
                ORDER BY s.id DESC
            """)
            schools = cursor.fetchall()
            return jsonify({"status": "success", "schools": schools}), 200
    except Exception as e:
        return jsonify({"status": "error", "schools": []}), 500
    finally:
        if connection: connection.close()

@app.route('/update_school_id', methods=['POST'])
def update_school_id():
    old_id = request.values.get('old_id')
    new_id = request.values.get('new_id')
    if not old_id or not new_id or not new_id.isdigit() or len(new_id) != 6:
        return jsonify({"status": "error", "message": "بيانات غير صحيحة"}), 400
    connection = get_db_connection()
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
            return jsonify({"status": "success", "message": "تم التحديث بنجاح"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": "خطأ في السيرفر"}), 500
    finally:
        if connection: connection.close()

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "School API is running successfully!"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
