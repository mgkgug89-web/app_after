import os
import secrets
import string
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# ========================================================
# ⚠️⚠️ إعدادات قاعدة البيانات ⚠️⚠️
# ========================================================
DB_HOST = "ضع_الـ Host_هنا"
DB_USER = "ضع_الـ Username_هنا"
DB_PASSWORD = "ضع_الـ Password_هنا"
DB_NAME = "ضع_الـ Database_هنا"
DB_PORT = "5432"

def get_db_connection():
    try:
        return psycopg2.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
            database=DB_NAME, port=DB_PORT,
            cursor_factory=RealDictCursor, connect_timeout=5
        )
    except Exception as e:
        print(f"Error connecting to PostgreSQL: {e}")
        return None

def init_db():
    connection = get_db_connection()
    if not connection:
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schools (
                    id SERIAL PRIMARY KEY,
                    school_id VARCHAR(6) NOT NULL UNIQUE,
                    school_name VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            connection.commit()
    except Exception as e:
        print(f"❌ Error creating tables: {e}")
    finally:
        if connection: connection.close()

@app.route('/verify', methods=['POST'])
def verify_school():
    school_id = request.values.get('school_id')
    if not school_id:
        return jsonify({"status": "error", "school_name": None, "message": "الرجاء إدخال إيدي المدرسة"}), 400
    connection = get_db_connection()
    if not connection:
        return jsonify({"status": "error", "school_name": None, "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT school_name FROM schools WHERE school_id = %s", (school_id,))
            result = cursor.fetchone()
            if result:
                return jsonify({"status": "success", "school_name": result['school_name'], "message": None}), 200
            return jsonify({"status": "error", "school_name": None, "message": "الإيدي غير صحيح"}), 200
    finally:
        if connection: connection.close()

@app.route('/verify_admin', methods=['POST'])
def verify_admin():
    return jsonify({"status": "success", "message": "تم التحقق"}), 200

@app.route('/generate_key', methods=['GET'])
def generate_key():
    alphabet = string.ascii_uppercase + string.digits
    key = ''.join(secrets.choice(alphabet) for _ in range(12))
    connection = get_db_connection()
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO admin_keys (key_value) VALUES (%s)", (key,))
            connection.commit()
        return jsonify({"status": "success", "key": key}), 200
    finally:
        if connection: connection.close()

@app.route('/create_school', methods=['POST'])
def create_school():
    # استخدام request.values لالتقاط البيانات سواء كانت Form أو Multipart بشكل دقيق
    school_name = request.values.get('school_name')
    admin_password = request.values.get('admin_password')
    
    if not school_name or not admin_password:
        return jsonify({"status": "error", "message": "الرجاء إدخال الاسم وكلمة المرور"}), 400

    connection = get_db_connection()
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500

    try:
        with connection.cursor() as cursor:
            # التحقق الصارم من المفتاح وأنه غير مستخدم (is_used = 0)
            cursor.execute("SELECT id FROM admin_keys WHERE key_value = %s AND is_used = 0", (admin_password.strip(),))
            key_row = cursor.fetchone()
            
            if not key_row:
                return jsonify({"status": "error", "message": "كلمة السر غير صحيحة أو تم استخدامها مسبقاً!"}), 400

            # توليد إيدي عشوائي 6 أرقام فريد
            while True:
                school_id = ''.join(secrets.choice(string.digits) for _ in range(6))
                cursor.execute("SELECT id FROM schools WHERE school_id = %s", (school_id,))
                if not cursor.fetchone():
                    break

            # إدخال المدرسة
            cursor.execute("INSERT INTO schools (school_id, school_name) VALUES (%s, %s)", (school_id, school_name))
            
            # تحديث المفتاح ليصبح مستخدماً بشكل نهائي
            cursor.execute("UPDATE admin_keys SET is_used = 1 WHERE id = %s", (key_row['id'],))
            
            connection.commit()

        return jsonify({"status": "success", "message": f"تم إنشاء المدرسة بنجاح! الإيدي: {school_id}"}), 200
    except Exception as e:
        print(f"Error: {e}")
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
            cursor.execute("SELECT school_id, school_name FROM schools ORDER BY id DESC")
            schools = cursor.fetchall()
            return jsonify({"status": "success", "schools": schools}), 200
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
            cursor.execute("SELECT id FROM schools WHERE school_id = %s", (new_id,))
            if cursor.fetchone():
                return jsonify({"status": "error", "message": "الإيدي مستخدم مسبقاً"}), 400
            cursor.execute("UPDATE schools SET school_id = %s WHERE school_id = %s", (new_id, old_id))
            connection.commit()
            return jsonify({"status": "success", "message": "تم التحديث بنجاح"}), 200
    finally:
        if connection: connection.close()

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "School API is running successfully!"}), 200

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
