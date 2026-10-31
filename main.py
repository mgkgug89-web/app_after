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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE schools ADD COLUMN IF NOT EXISTS admin_device_id VARCHAR(255);")
    cursor.execute("ALTER TABLE schools ADD COLUMN IF NOT EXISTS admin_password VARCHAR(255) DEFAULT '123456';")
    cursor.execute("ALTER TABLE schools ADD COLUMN IF NOT EXISTS is_closed SMALLINT DEFAULT 0;")
    cursor.execute("ALTER TABLE schools ADD COLUMN IF NOT EXISTS close_password VARCHAR(255) DEFAULT '1234';")

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
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS classes (
            id SERIAL PRIMARY KEY,
            school_id VARCHAR(6) NOT NULL,
            class_name VARCHAR(100) NOT NULL,
            class_password VARCHAR(100) NOT NULL,
            image_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ✅ جدول طلبات الزوار
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS visitors (
            id SERIAL PRIMARY KEY,
            school_id VARCHAR(6) NOT NULL,
            visitor_device_id VARCHAR(255) NOT NULL,
            status VARCHAR(50) DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            except Exception:
                pass
            finally:
                conn.close()

def refresh_cache():
    global cached_schools
    all_schools = []
    for i in range(5):
        connection = get_db_connection(str(i * 2)) 
        if not connection:
            continue
        try:
            with connection.cursor() as cursor:
                create_tables_if_not_exist(cursor)
                # ✅ جلب البيانات كاملة بدقة لضمان مزامنة حالة الإغلاق والإيدي
                cursor.execute("""
                    SELECT s.school_id, s.school_name, s.admin_device_id, s.admin_password, COALESCE(s.is_closed, 0) as is_closed, si.image_url,
                    (SELECT COUNT(*) FROM classes c WHERE c.school_id = s.school_id) as classes_count
                    FROM schools s 
                    LEFT JOIN school_images si ON s.id = si.school_id 
                    ORDER BY s.id DESC
                """)
                all_schools.extend(cursor.fetchall())
        except Exception:
            pass
        finally:
            if connection: connection.close()
            
    all_schools.sort(key=lambda x: x['school_id'], reverse=True)
    cached_schools = all_schools

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
            cursor.execute("SELECT school_name, COALESCE(is_closed, 0) as is_closed FROM schools WHERE school_id = %s", (school_id,))
            result = cursor.fetchone()
            if result:
                return jsonify({"status": "success", "school_name": result['school_name'], "is_closed": result['is_closed'], "message": None}), 200
            return jsonify({"status": "error", "school_name": None, "message": "الإيدي غير صحيح"}), 200
    except Exception:
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

@app.route('/verify_school_admin', methods=['POST'])
def verify_school_admin():
    school_id = request.values.get('school_id')
    password = request.values.get('password')
    if not school_id or not password:
        return jsonify({"status": "error", "message": "بيانات غير مكتملة"}), 400
    
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT admin_password FROM schools WHERE school_id = %s", (school_id,))
            row = cursor.fetchone()
            if row and row['admin_password'] == password:
                return jsonify({"status": "success", "message": "تم التحقق بنجاح"}), 200
            return jsonify({"status": "error", "message": "كلمة سر المدير غير صحيحة"}), 401
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/update_school_admin_password', methods=['POST'])
def update_school_admin_password():
    school_id = request.values.get('school_id')
    new_password = request.values.get('new_password')
    if not school_id or not new_password:
        return jsonify({"status": "error", "message": "بيانات غير كافية"}), 400
    
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE schools SET admin_password = %s WHERE school_id = %s", (new_password, school_id))
            connection.commit()
            refresh_cache()
            return jsonify({"status": "success", "message": "تم تحديث كلمة سر المدير بنجاح"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

# ✅ التحكم بحالة المدرسة (إغلاق / فتح) وتحديث الكاش فوراً بضمان كامل
@app.route('/set_school_status', methods=['POST'])
def set_school_status():
    school_id = request.values.get('school_id')
    is_closed = request.values.get('is_closed')
    close_password = request.values.get('close_password', '1234')

    if not school_id or is_closed is None:
        return jsonify({"status": "error", "message": "بيانات غير مكتملة"}), 400

    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            if int(is_closed) == 1:
                cursor.execute("UPDATE schools SET is_closed = 1, close_password = %s WHERE school_id = %s", (close_password, school_id))
            else:
                cursor.execute("UPDATE schools SET is_closed = 0 WHERE school_id = %s", (school_id,))
            connection.commit()
            refresh_cache()  # تحديث الكاش الفوري لكي يتعرف السيرفر والتطبيق على حالة الإغلاق الجديدة
            return jsonify({"status": "success", "message": "تم تحديث حالة المدرسة بنجاح"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/verify_school_close_pass', methods=['POST'])
def verify_school_close_pass():
    school_id = request.values.get('school_id')
    password = request.values.get('password')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT close_password FROM schools WHERE school_id = %s", (school_id,))
            row = cursor.fetchone()
            if row and row['close_password'] == password:
                return jsonify({"status": "success", "message": "كلمة السر صحيحة"}), 200
            return jsonify({"status": "error", "message": "كلمة السر غير صحيحة"}), 401
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

# ✅ جلب تفاصيل المدرسة المحدثة (تأكيد حالة الإغلاق والإيدي الحقيقي من قاعدة البيانات)
@app.route('/get_school_details', methods=['POST'])
def get_school_details():
    school_id = request.values.get('school_id')
    if not school_id:
        return jsonify({"status": "error", "message": "إيدي المدرسة مطلوب"}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT school_id, school_name, COALESCE(is_closed, 0) as is_closed FROM schools WHERE school_id = %s", (school_id,))
            school = cursor.fetchone()
            if school:
                return jsonify({"status": "success", "school": school}), 200
            return jsonify({"status": "error", "message": "المدرسة غير موجودة"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

# ✅ إدارة طلبات الزوار
@app.route('/request_visit', methods=['POST'])
def request_visit():
    school_id = request.values.get('school_id')
    visitor_device_id = request.values.get('visitor_device_id')
    if not school_id or not visitor_device_id:
        return jsonify({"status": "error", "message": "بيانات غير كافية"}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            cursor.execute("INSERT INTO visitors (school_id, visitor_device_id) VALUES (%s, %s)", (school_id, visitor_device_id))
            connection.commit()
            return jsonify({"status": "success", "message": "تم إرسال الطلب بنجاح"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/get_visitors', methods=['POST', 'GET'])
def get_visitors():
    school_id = request.values.get('school_id')
    if not school_id:
        return jsonify({"status": "error", "visitors": []}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "visitors": []}), 500
    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            cursor.execute("SELECT id, visitor_device_id, status FROM visitors WHERE school_id = %s AND status = 'pending' ORDER BY id DESC", (school_id,))
            visitors = cursor.fetchall()
            return jsonify({"status": "success", "visitors": visitors}), 200
    except Exception:
        return jsonify({"status": "error", "visitors": []}), 500
    finally:
        if connection: connection.close()

@app.route('/approve_visitor', methods=['POST'])
def approve_visitor():
    school_id = request.values.get('school_id')
    visitor_id = request.values.get('visitor_id')
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE visitors SET status = 'approved' WHERE id = %s", (visitor_id,))
            connection.commit()
            return jsonify({"status": "success", "message": "تمت الموافقة"}), 200
    except Exception:
        return jsonify({"status": "error", "message": "خطأ"}), 500
    finally:
        if connection: connection.close()

@app.route('/generate_key', methods=['GET'])
def generate_key():
    alphabet = string.ascii_uppercase + string.digits
    key = ''.join(secrets.choice(alphabet) for _ in range(12))
    
    connection = get_db_connection("0")
    if not connection:
        return jsonify({"status": "error", "message": "السيرفر لا يستطيع الاتصال"}), 500
        
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
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/create_school', methods=['POST'])
def create_school():
    school_name = request.values.get('school_name')
    admin_password = request.values.get('admin_password')
    device_id = request.values.get('device_id', '')
    image_base64 = request.values.get('image_base64', '')
    
    if not school_name or not admin_password:
        return jsonify({"status": "error", "message": "الرجاء إدخال الاسم وكلمة المرور"}), 400

    school_id = None
    for _ in range(20):
        temp_id = ''.join(secrets.choice(string.digits) for _ in range(6))
        conn = get_db_connection(temp_id)
        if not conn: 
            continue
        try:
            with conn.cursor() as cursor:
                create_tables_if_not_exist(cursor)
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
            create_tables_if_not_exist(cursor)
            cursor.execute("SELECT id FROM admin_keys WHERE key_value = %s AND is_used = 0", (admin_password.strip(),))
            key_row = cursor.fetchone()
            
            if not key_row:
                return jsonify({"status": "error", "message": "كلمة السر غير صحيحة أو تم استخدامها مسبقاً!"}), 400

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
                except Exception:
                    pass

            cursor.execute("INSERT INTO schools (school_id, school_name, admin_device_id, admin_password, is_closed) VALUES (%s, %s, %s, %s, 0) RETURNING id", (school_id, school_name, device_id, "123456"))
            new_school_db_id = cursor.fetchone()['id']

            if image_url:
                cursor.execute("INSERT INTO school_images (school_id, image_url) VALUES (%s, %s)", (new_school_db_id, image_url))
            
            cursor.execute("DELETE FROM admin_keys WHERE id = %s", (key_row['id'],))
            connection.commit()

        refresh_cache()
        return jsonify({"status": "success", "message": f"تم إنشاء المدرسة بنجاح! الإيدي: {school_id}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/get_schools', methods=['GET'])
def get_schools():
    refresh_cache()  # تحديث الكاش عند كل جلب لضمان أحدث حالة للمدارس المغلقة
    return jsonify({"status": "success", "schools": cached_schools}), 200

@app.route('/get_classes', methods=['POST'])
def get_classes():
    school_id = request.values.get('school_id')
    if not school_id:
        return jsonify({"status": "error", "classes": []}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "classes": []}), 500
    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            cursor.execute("SELECT id, class_name, class_password, image_url FROM classes WHERE school_id = %s ORDER BY id DESC", (school_id,))
            classes = cursor.fetchall()
            return jsonify({"status": "success", "classes": classes}), 200
    except Exception:
        return jsonify({"status": "error", "classes": []}), 500
    finally:
        if connection: connection.close()

@app.route('/create_class', methods=['POST'])
def create_class():
    school_id = request.values.get('school_id')
    class_name = request.values.get('class_name')
    class_password = request.values.get('class_password')
    image_base64 = request.values.get('image_base64', '')

    if not school_id or not class_name or not class_password:
        return jsonify({"status": "error", "message": "بيانات غير مكتملة"}), 400

    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            image_url = ""
            if image_base64:
                try:
                    if "," in image_base64:
                        image_base64 = image_base64.split(",")[1]
                    image_bytes = base64.b64decode(image_base64)
                    files = {'fileToUpload': ('class_image.jpg', image_bytes, 'image/jpeg')}
                    data = {'reqtype': 'fileupload'}
                    response = requests.post("https://catbox.moe/user/api.php", files=files, data=data)
                    if response.status_code == 200 and response.text.startswith("https://"):
                        image_url = response.text.strip()
                except Exception:
                    pass

            cursor.execute("INSERT INTO classes (school_id, class_name, class_password, image_url) VALUES (%s, %s, %s, %s)", (school_id, class_name, class_password, image_url))
            connection.commit()
            refresh_cache()
            return jsonify({"status": "success", "message": "تم إنشاء الفصل بنجاح"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/delete_class', methods=['POST'])
def delete_class():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    if not school_id or not class_id:
        return jsonify({"status": "error", "message": "بيانات غير كافية"}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM classes WHERE id = %s AND school_id = %s", (class_id, school_id))
            connection.commit()
            refresh_cache()
            return jsonify({"status": "success", "message": "تم الحذف بنجاح"}), 200
    exceptException:
        return jsonify({"status": "error", "message": "خطأ في الحذف"}), 500
    finally:
        if connection: connection.close()

@app.route('/update_class_password', methods=['POST'])
def update_class_password():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    new_password = request.values.get('new_password')
    if not school_id or not class_id or not new_password:
        return jsonify({"status": "error", "message": "بيانات غير كافية"}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE classes SET class_password = %s WHERE id = %s AND school_id = %s", (new_password, class_id, school_id))
            connection.commit()
            return jsonify({"status": "success", "message": "تم تحديث كلمة السر بنجاح"}), 200
    except Exception:
        return jsonify({"status": "error", "message": "خطأ في التحديث"}), 500
    finally:
        if connection: connection.close()

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "School & Classes API is running successfully!"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
