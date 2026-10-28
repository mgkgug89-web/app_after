import os
import pymysql
import secrets
import string
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# ========================================================
# ⚠️⚠️ إعدادات قاعدة البيانات ⚠️⚠️
# ========================================================
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_USER = os.environ.get('DB_USER', 'ضع_اسم_المستخدم_هنا')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'ضع_كلمة_المرور_هنا')
DB_NAME = os.environ.get('DB_NAME', 'ضع_اسم_قاعدة_البيانات_هنا')

MASTER_PASSWORD = "2573486195715653N"

def get_db_connection():
    try:
        return pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
            database=DB_NAME, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor, connect_timeout=5
        )
    except pymysql.MySQLError as e:
        print(f"Error connecting to MySQL: {e}")
        return None

# ========================================================
# ✅ إنشاء الجداول تلقائياً (المدارس + المفاتيح)
# ========================================================
def init_db():
    connection = get_db_connection()
    if not connection:
        print("❌ Could not connect to database to create tables.")
        return
    try:
        with connection.cursor() as cursor:
            # جدول المدارس
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schools (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    school_id VARCHAR(6) NOT NULL UNIQUE,
                    school_name VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # جدول المفاتيح المولدة
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin_keys (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    key_value VARCHAR(20) NOT NULL UNIQUE,
                    is_used TINYINT(1) DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connection.commit()
            print("✅ Database tables created/verified successfully.")
    except Exception as e:
        print(f"❌ Error creating tables: {e}")
    finally:
        if connection.open: connection.close()

# ========================================================
# ✅ المسارات
# ========================================================

@app.route('/verify', methods=['POST'])
def verify_school():
    school_id = request.form.get('school_id')
    if not school_id or not school_id.strip():
        return jsonify({"status": "error", "school_name": None, "message": "الرجاء إدخال إيدي المدرسة"}), 400
    school_id = school_id.strip()
    connection = get_db_connection()
    if not connection:
        return jsonify({"status": "error", "school_name": None, "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT school_name FROM schools WHERE school_id = %s LIMIT 1", (school_id,))
            result = cursor.fetchone()
            if result:
                return jsonify({"status": "success", "school_name": result['school_name'], "message": None}), 200
            else:
                return jsonify({"status": "error", "school_name": None, "message": "الإيدي غير صحيح"}), 200
    except Exception as e:
        print(f"Database query error: {e}")
        return jsonify({"status": "error", "school_name": None, "message": "خطأ في السيرفر"}), 500
    finally:
        if connection.open: connection.close()

@app.route('/verify_admin', methods=['POST'])
def verify_admin():
    password = request.form.get('password')
    if not password:
        return jsonify({"status": "error", "message": "الرجاء إدخال كلمة السر"}), 400
    if password == MASTER_PASSWORD:
        return jsonify({"status": "success", "message": "تم التحقق بنجاح"}), 200
    else:
        return jsonify({"status": "error", "message": "كلمة السر غير صحيحة"}), 401

# ========================================================
# ✅ توليد مفتاح وحفظه في قاعدة البيانات
# ========================================================
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
    except Exception as e:
        print(f"Error saving key: {e}")
        return jsonify({"status": "error", "message": "خطأ في حفظ المفتاح"}), 500
    finally:
        if connection.open: connection.close()

# ========================================================
# ✅ إنشاء مدرسة (يتحقق من المفتاح ويحذفه بعد الاستخدام)
# ========================================================
@app.route('/create_school', methods=['POST'])
def create_school():
    school_name = request.form.get('school_name')
    admin_password = request.form.get('admin_password')

    if not school_name or not admin_password:
        return jsonify({"status": "error", "message": "الرجاء إدخال اسم المدرسة وكلمة المرور"}), 400

    connection = get_db_connection()
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500

    try:
        with connection.cursor() as cursor:
            # التحقق من أن المفتاح موجود ولم يُستخدم
            cursor.execute("SELECT id FROM admin_keys WHERE key_value = %s AND is_used = 0", (admin_password,))
            key_row = cursor.fetchone()
            
            if not key_row:
                return jsonify({"status": "error", "message": "كلمة السر غير صحيحة أو تم استخدامها مسبقاً"}), 401

            # توليد school_id عشوائي 6 أرقام
            while True:
                school_id = ''.join(secrets.choice(string.digits) for _ in range(6))
                cursor.execute("SELECT id FROM schools WHERE school_id = %s", (school_id,))
                if not cursor.fetchone():
                    break

            # إدخال المدرسة
            cursor.execute("INSERT INTO schools (school_id, school_name) VALUES (%s, %s)", (school_id, school_name))
            
            # تحديث المفتاح ليصبح مستخدماً
            cursor.execute("UPDATE admin_keys SET is_used = 1 WHERE id = %s", (key_row['id'],))
            
            connection.commit()

        return jsonify({"status": "success", "message": f"تم إنشاء المدرسة بنجاح! الإيدي الخاص بها: {school_id}"}), 200
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"status": "error", "message": "خطأ في السيرفر"}), 500
    finally:
        if connection.open: connection.close()

@app.route('/get_schools', methods=['GET'])
def get_schools():
    connection = get_db_connection()
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال", "schools": []}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT school_id, school_name FROM schools ORDER BY id DESC")
            schools = cursor.fetchall()
            return jsonify({"status": "success", "schools": schools}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": "خطأ في جلب المدارس", "schools": []}), 500
    finally:
        if connection.open: connection.close()

@app.route('/update_school_id', methods=['POST'])
def update_school_id():
    old_id = request.form.get('old_id')
    new_id = request.form.get('new_id')
    if not old_id or not new_id:
        return jsonify({"status": "error", "message": "الرجاء إدخال الإيدي القديم والجديد"}), 400
    if not new_id.isdigit() or len(new_id) != 6:
        return jsonify({"status": "error", "message": "الإيدي الجديد يجب أن يكون 6 أرقام"}), 400
    connection = get_db_connection()
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM schools WHERE school_id = %s", (new_id,))
            if cursor.fetchone():
                return jsonify({"status": "error", "message": "هذا الإيدي مستخدم بالفعل"}), 400
            cursor.execute("UPDATE schools SET school_id = %s WHERE school_id = %s", (new_id, old_id))
            connection.commit()
            if cursor.rowcount > 0:
                return jsonify({"status": "success", "message": "تم تحديث الإيدي بنجاح"}), 200
            else:
                return jsonify({"status": "error", "message": "لم يتم العثور على المدرسة"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": "خطأ أثناء التحديث"}), 500
    finally:
        if connection.open: connection.close()

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "School API is running successfully!"}), 200

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
