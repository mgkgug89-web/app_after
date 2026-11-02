import os
import secrets
import string
import time
from datetime import timedelta
import psycopg2
import requests
import base64
import io
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from livekit import api

load_dotenv()
app = Flask(__name__)
CORS(app)
app.json.ensure_ascii = False

ADMIN_SECRET_PASSWORD = "2573486195715653N"
cached_schools = []

# 🆕 مفاتيح LiveKit
LIVEKIT_API_KEY = "APIbmfvPM9vQA4s"
LIVEKIT_API_SECRET = "8p7SAFQiWjhr5YN86h4fnuwILLcMAZ1fkgYn4ri4rvE"
LIVEKIT_URL = "wss://distancelearning-mlptg6d9.livekit.cloud"

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

# ============================================================
# ✅ إنشاء الجداول + إصلاح الأعمدة الناقصة تلقائياً
# ============================================================
def create_tables_if_not_exist(cursor):
    # جدول المدارس
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
    cursor.execute("ALTER TABLE schools ADD COLUMN IF NOT EXISTS leader_password VARCHAR(255) DEFAULT '123456';")

    # صور المدارس
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS school_images (
            school_id INT PRIMARY KEY REFERENCES schools(id) ON DELETE CASCADE,
            image_url TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # مفاتيح الإدارة
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_keys (
            id SERIAL PRIMARY KEY,
            key_value VARCHAR(20) NOT NULL UNIQUE,
            is_used SMALLINT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # الفصول
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

    # الزوار
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS visitors (
            id SERIAL PRIMARY KEY,
            school_id VARCHAR(6) NOT NULL,
            visitor_device_id VARCHAR(255) NOT NULL,
            status VARCHAR(50) DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ملفات الطلاب
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

    # 🆕 جدول المعلمين - نُنشئه بدون أي قيد NOT NULL على teacher_id
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS class_teachers (
            id SERIAL PRIMARY KEY,
            teacher_user_id VARCHAR(50),
            class_id INT NOT NULL,
            school_id VARCHAR(6) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # محاولة إضافة الأعمدة إذا لم تكن موجودة (لن يؤثر إذا كانت موجودة)
    try:
        cursor.execute("ALTER TABLE class_teachers ADD COLUMN IF NOT EXISTS teacher_user_id VARCHAR(50);")
    except Exception:
        pass
    
    # 🆕 إزالة القيد NOT NULL من teacher_id إذا كان موجوداً (لكي لا يفشل الإدخال)
    try:
        cursor.execute("ALTER TABLE class_teachers ALTER COLUMN teacher_id DROP NOT NULL;")
    except Exception:
        pass

    # الحضور في الفصول
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

    # طلبات المايك
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mic_requests (
            id SERIAL PRIMARY KEY,
            school_id VARCHAR(6) NOT NULL,
            class_id INT NOT NULL,
            student_device_id VARCHAR(255) NOT NULL,
            student_user_id VARCHAR(50),
            student_name VARCHAR(255),
            student_image TEXT,
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
            except Exception as e:
                print(f"❌ Init error DB_{i}: {e}")
                try:
                    conn.rollback()
                except:
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
                cursor.execute("""
                    SELECT s.school_id, s.school_name, s.admin_device_id, s.admin_password, COALESCE(s.is_closed, 0) as is_closed, si.image_url,
                    (SELECT COUNT(*) FROM classes c WHERE c.school_id = s.school_id) as classes_count
                    FROM schools s 
                    LEFT JOIN school_images si ON s.id = si.school_id 
                    ORDER BY s.id DESC
                """)
                all_schools.extend(cursor.fetchall())
                connection.commit()
        except Exception:
            try:
                connection.rollback()
            except:
                pass
        finally:
            if connection: connection.close()
    all_schools.sort(key=lambda x: x['school_id'], reverse=True)
    cached_schools = all_schools

init_all_databases()
refresh_cache()

# ============================================================
# 🆕 توليد توكن LiveKit
# ============================================================
@app.route('/get_livekit_token', methods=['POST'])
def get_livekit_token():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    user_id = request.values.get('user_id')
    user_name = request.values.get('user_name', 'User')

    if not school_id or not class_id or not user_id:
        return jsonify({"status": "error", "message": "بيانات ناقصة"}), 400

    room_name = f"class_{school_id}_{class_id}"

    try:
        token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
            .with_identity(user_id) \
            .with_name(user_name) \
            .with_grants(api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            )) \
            .with_ttl(timedelta(seconds=3600)) \
            .to_jwt()

        return jsonify({
            "status": "success",
            "token": token,
            "room": room_name,
            "server_url": LIVEKIT_URL
        }), 200
    except Exception as e:
        print(f"❌ LiveKit Token Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================
# 🆕 Endpoint إصلاح كل الجداول
# ============================================================
@app.route('/fix_all_tables', methods=['GET', 'POST'])
def fix_all_tables():
    secret = request.values.get('secret')
    if secret != ADMIN_SECRET_PASSWORD:
        return jsonify({"status": "error", "message": "غير مصرح"}), 401
    
    results = []
    all_success = True
    
    for i in range(5):
        conn = get_db_connection(str(i * 2))
        if not conn:
            results.append({"db_index": i, "status": "skipped", "reason": "no connection"})
            all_success = False
            continue
        
        db_result = {"db_index": i, "steps": {}}
        
        try:
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS class_teachers (
                        id SERIAL PRIMARY KEY,
                        teacher_user_id VARCHAR(50),
                        class_id INT NOT NULL,
                        school_id VARCHAR(6) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.commit()
                db_result["steps"]["create_table"] = "success"
            except Exception as e:
                conn.rollback()
                db_result["steps"]["create_table"] = f"error: {str(e)}"
            
            try:
                cursor.execute("ALTER TABLE class_teachers ADD COLUMN IF NOT EXISTS teacher_user_id VARCHAR(50);")
                conn.commit()
                db_result["steps"]["add_column"] = "success"
            except Exception as e:
                conn.rollback()
                db_result["steps"]["add_column"] = f"error: {str(e)}"
            
            # 🆕 إزالة NOT NULL من teacher_id (العمود القديم)
            try:
                cursor.execute("ALTER TABLE class_teachers ALTER COLUMN teacher_id DROP NOT NULL;")
                conn.commit()
                db_result["steps"]["drop_not_null_teacher_id"] = "success"
            except Exception as e:
                conn.rollback()
                db_result["steps"]["drop_not_null_teacher_id"] = f"error: {str(e)}"
            
            try:
                cursor.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint 
                            WHERE conname = 'class_teachers_unique_idx'
                        ) THEN
                            ALTER TABLE class_teachers 
                            ADD CONSTRAINT class_teachers_unique_idx 
                            UNIQUE (teacher_user_id, class_id);
                        END IF;
                    EXCEPTION WHEN OTHERS THEN
                        NULL;
                    END $$;
                """)
                conn.commit()
                db_result["steps"]["add_constraint"] = "success"
            except Exception as e:
                conn.rollback()
                db_result["steps"]["add_constraint"] = f"error: {str(e)}"
            
            try:
                cursor.execute("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = 'class_teachers' AND column_name = 'teacher_user_id'
                """)
                has_col = cursor.fetchone() is not None
                db_result["teacher_user_id_exists"] = has_col
                if not has_col:
                    all_success = False
            except Exception as e:
                db_result["verification_error"] = str(e)
                all_success = False
            
            cursor.close()
            db_result["status"] = "success"
            results.append(db_result)
            
        except Exception as e:
            try:
                conn.rollback()
            except:
                pass
            results.append({"db_index": i, "status": "error", "error": str(e)})
            all_success = False
        finally:
            if conn: conn.close()
    
    return jsonify({
        "status": "success" if all_success else "partial",
        "message": "✅ تم إصلاح جميع الجداول بنجاح!" if all_success else "⚠️ تم الإصلاح جزئياً - راجع النتائج",
        "results": results
    }), 200

# ============================================================
# 🆕 Endpoint لفحص وجود user_id
# ============================================================
@app.route('/check_user_id', methods=['POST', 'GET'])
def check_user_id():
    user_id = request.values.get('user_id')
    if not user_id:
        return jsonify({"status": "error", "message": "الرجاء إرسال user_id"}), 400

    variants = [user_id]
    if not user_id.startswith("STU-"):
        variants.append(f"STU-{user_id}")
    else:
        variants.append(user_id.replace("STU-", ""))

    results = {}
    for i in range(5):
        conn = get_db_connection(str(i * 2))
        if not conn:
            continue
        try:
            with conn.cursor() as cursor:
                for variant in variants:
                    try:
                        cursor.execute("SELECT device_id, user_id, full_name, age, school_name, image_url, updated_at FROM student_profiles WHERE user_id = %s", (variant,))
                        profile = cursor.fetchone()
                        if profile:
                            results[f"DB_{i}_student_profiles_{variant}"] = {
                                "device_id": profile['device_id'],
                                "user_id": profile['user_id'],
                                "full_name": profile['full_name'],
                                "age": profile['age'],
                                "school_name": profile['school_name'],
                                "image_url": profile['image_url'],
                                "updated_at": profile['updated_at'].isoformat() if profile['updated_at'] else None
                            }
                    except Exception as e:
                        results[f"DB_{i}_student_profiles_error"] = str(e)

                    try:
                        cursor.execute("SELECT school_id, class_id, device_id, user_id, last_seen FROM class_presence WHERE user_id = %s", (variant,))
                        presences = cursor.fetchall()
                        if presences:
                            results[f"DB_{i}_class_presence_{variant}"] = [
                                {"school_id": p['school_id'], "class_id": p['class_id'], "device_id": p['device_id'], "last_seen": p['last_seen'].isoformat() if p['last_seen'] else None}
                                for p in presences
                            ]
                    except Exception as e:
                        results[f"DB_{i}_class_presence_error"] = str(e)

                    try:
                        cursor.execute("SELECT id, school_id, class_id, student_name FROM mic_requests WHERE student_user_id = %s", (variant,))
                        mics = cursor.fetchall()
                        if mics:
                            results[f"DB_{i}_mic_requests_{variant}"] = [
                                {"id": m['id'], "school_id": m['school_id'], "class_id": m['class_id'], "student_name": m['student_name']}
                                for m in mics
                            ]
                    except Exception as e:
                        results[f"DB_{i}_mic_requests_error"] = str(e)

                    try:
                        cursor.execute("SELECT teacher_user_id, class_id, school_id FROM class_teachers WHERE teacher_user_id = %s", (variant,))
                        teachers = cursor.fetchall()
                        if teachers:
                            results[f"DB_{i}_class_teachers_{variant}"] = [
                                {"teacher_user_id": t['teacher_user_id'], "class_id": t['class_id'], "school_id": t['school_id']}
                                for t in teachers
                            ]
                    except Exception as e:
                        results[f"DB_{i}_class_teachers_error"] = str(e)
        except Exception as e:
            results[f"DB_{i}_error"] = str(e)
        finally:
            conn.close()

    if not results:
        return jsonify({
            "status": "not_found",
            "message": f"❌ الـ ID '{user_id}' غير موجود في أي جدول بأي قاعدة بيانات",
            "searched_variants": variants
        }), 200

    has_errors = any("error" in key.lower() for key in results.keys())
    has_data = any("student_profiles" in key or "class_presence" in key or "class_teachers" in key for key in results.keys())

    if has_errors and not has_data:
        return jsonify({
            "status": "error",
            "message": "❌ وُجدت أخطاء في قاعدة البيانات",
            "errors": {k: v for k, v in results.items() if "error" in k.lower()},
            "hint": "قم بتنفيذ /fix_all_tables أولاً"
        }), 200

    return jsonify({
        "status": "found",
        "message": "✅ تم العثور على الـ ID في قاعدة البيانات",
        "user_id": user_id,
        "searched_variants": variants,
        "results": results
    }), 200

# ============================================================
# 🆕 Endpoint مؤقت لترحيل البيانات القديمة
# ============================================================
@app.route('/migrate_user_ids', methods=['POST'])
def migrate_user_ids():
    secret = request.values.get('secret')
    if secret != ADMIN_SECRET_PASSWORD:
        return jsonify({"status": "error", "message": "غير مصرح"}), 401
    
    results = []
    for i in range(5):
        conn = get_db_connection(str(i * 2))
        if not conn:
            continue
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE student_profiles 
                    SET user_id = 'STU-' || user_id 
                    WHERE user_id IS NOT NULL 
                      AND user_id NOT LIKE 'STU-%' 
                      AND LENGTH(user_id) = 6
                """)
                cnt1 = cursor.rowcount

                cursor.execute("""
                    UPDATE class_presence 
                    SET user_id = 'STU-' || user_id 
                    WHERE user_id IS NOT NULL 
                      AND user_id NOT LIKE 'STU-%' 
                      AND LENGTH(user_id) = 6
                """)
                cnt2 = cursor.rowcount

                cursor.execute("""
                    UPDATE mic_requests 
                    SET student_user_id = 'STU-' || student_user_id 
                    WHERE student_user_id IS NOT NULL 
                      AND student_user_id NOT LIKE 'STU-%' 
                      AND LENGTH(student_user_id) = 6
                """)
                cnt3 = cursor.rowcount

                try:
                    cursor.execute("""
                        UPDATE class_teachers 
                        SET teacher_user_id = 'STU-' || teacher_user_id 
                        WHERE teacher_user_id IS NOT NULL 
                          AND teacher_user_id NOT LIKE 'STU-%' 
                          AND LENGTH(teacher_user_id) = 6
                    """)
                    cnt4 = cursor.rowcount
                except Exception:
                    cnt4 = 0

                conn.commit()
                results.append({
                    "db_index": i,
                    "student_profiles": cnt1,
                    "class_presence": cnt2,
                    "mic_requests": cnt3,
                    "class_teachers": cnt4
                })
        except Exception as e:
            results.append({"db_index": i, "error": str(e)})
        finally:
            conn.close()
    
    return jsonify({"status": "success", "results": results}), 200

@app.route('/check_device_id', methods=['POST', 'GET'])
def check_device_id():
    device_id = request.values.get('device_id')
    if not device_id:
        return jsonify({"status": "error", "message": "الرجاء إرسال device_id"}), 400

    connection = get_db_connection("0")
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM student_profiles WHERE device_id = %s", (device_id,))
            profile = cursor.fetchone()
            if profile:
                return jsonify({
                    "status": "found",
                    "message": "✅ الـ device_id موجود",
                    "profile": {
                        "device_id": profile['device_id'],
                        "user_id": profile['user_id'],
                        "full_name": profile['full_name'],
                        "age": profile['age'],
                        "school_name": profile['school_name'],
                        "image_url": profile['image_url'],
                        "updated_at": profile['updated_at'].isoformat() if profile['updated_at'] else None
                    }
                }), 200
            return jsonify({
                "status": "not_found",
                "message": f"❌ الـ device_id '{device_id}' غير موجود في القاعدة"
            }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

# ============================================================
# باقي الـ Endpoints
# ============================================================

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
                    data = {'reqtype': 'fileupload'}
                    response = requests.post("https://catbox.moe/user/api.php", files=files, data=data)
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
    if not device_id:
        return jsonify({"status": "error", "message": "معرف الجهاز مطلوب"}), 400
    connection = get_db_connection("0")
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            cursor.execute("SELECT device_id, user_id, full_name, age, school_name, image_url FROM student_profiles WHERE device_id = %s", (device_id,))
            profile = cursor.fetchone()
            if profile:
                return jsonify({"status": "success", "profile": profile}), 200
            return jsonify({"status": "error", "profile": None, "message": "لا توجد بيانات محفوظة لهذا الجهاز"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

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
            refresh_cache()
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
    refresh_cache()
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
            cursor.execute("SELECT id, school_id, class_name, class_password, image_url FROM classes WHERE school_id = %s ORDER BY id DESC", (school_id,))
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
    except Exception:
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

# ============================================================
# 🆕 register_teachers - النسخة النهائية (تدعم teacher_id و teacher_user_id)
# ============================================================
@app.route('/register_teachers', methods=['POST'])
def register_teachers():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    teacher_ids = request.values.get('teacher_ids', '')
    if not school_id or not class_id or not teacher_ids:
        return jsonify({"status": "error", "message": "بيانات غير مكتملة"}), 400
    
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    
    try:
        ids_list = [tid.strip() for tid in teacher_ids.split(",") if tid.strip()]
        if not ids_list:
            return jsonify({"status": "error", "message": "لا توجد IDs صالحة"}), 400
        
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            
            # 🆕 اكتشاف بنية الجدول
            cursor.execute("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'class_teachers'
            """)
            existing_columns = [row['column_name'] for row in cursor.fetchall()]
            has_teacher_id = 'teacher_id' in existing_columns
            has_teacher_user_id = 'teacher_user_id' in existing_columns
            
            added_count = 0
            invalid_ids = []
            failed_inserts = []
            
            for raw_tid in ids_list:
                tid = raw_tid
                found_id = None

                # البحث في student_profiles
                cursor.execute("SELECT user_id FROM student_profiles WHERE user_id = %s LIMIT 1", (tid,))
                r = cursor.fetchone()
                if r:
                    found_id = r['user_id']

                if not found_id and not tid.startswith("STU-"):
                    alt = f"STU-{tid}"
                    cursor.execute("SELECT user_id FROM student_profiles WHERE user_id = %s LIMIT 1", (alt,))
                    r = cursor.fetchone()
                    if r:
                        found_id = r['user_id']

                if not found_id and tid.startswith("STU-"):
                    alt = tid.replace("STU-", "")
                    cursor.execute("SELECT user_id FROM student_profiles WHERE user_id = %s LIMIT 1", (alt,))
                    r = cursor.fetchone()
                    if r:
                        found_id = r['user_id']

                # البحث في class_presence
                if not found_id:
                    cursor.execute("SELECT user_id FROM class_presence WHERE user_id = %s AND class_id = %s LIMIT 1", (tid, int(class_id)))
                    r = cursor.fetchone()
                    if r:
                        found_id = r['user_id']
                if not found_id and not tid.startswith("STU-"):
                    alt = f"STU-{tid}"
                    cursor.execute("SELECT user_id FROM class_presence WHERE user_id = %s AND class_id = %s LIMIT 1", (alt, int(class_id)))
                    r = cursor.fetchone()
                    if r:
                        found_id = r['user_id']
                if not found_id and tid.startswith("STU-"):
                    alt = tid.replace("STU-", "")
                    cursor.execute("SELECT user_id FROM class_presence WHERE user_id = %s AND class_id = %s LIMIT 1", (alt, int(class_id)))
                    r = cursor.fetchone()
                    if r:
                        found_id = r['user_id']

                # البحث في mic_requests
                if not found_id:
                    cursor.execute("SELECT student_user_id FROM mic_requests WHERE student_user_id = %s LIMIT 1", (tid,))
                    r = cursor.fetchone()
                    if r:
                        found_id = r['student_user_id']

                if not found_id:
                    invalid_ids.append(tid)
                    continue

                # 🆕 التحقق من وجوده مسبقاً (بأي عمود متاح)
                try:
                    if has_teacher_user_id:
                        cursor.execute(
                            "SELECT id FROM class_teachers WHERE teacher_user_id = %s AND class_id = %s",
                            (found_id, int(class_id))
                        )
                        existing = cursor.fetchone()
                        if existing:
                            continue
                    elif has_teacher_id:
                        cursor.execute(
                            "SELECT id FROM class_teachers WHERE teacher_id = %s AND class_id = %s",
                            (found_id, int(class_id))
                        )
                        existing = cursor.fetchone()
                        if existing:
                            continue
                    
                    # 🆕 إدخال ديناميكي حسب الأعمدة الموجودة
                    if has_teacher_id and has_teacher_user_id:
                        # كلا العمودين موجودان - نملأ كليهما
                        cursor.execute("""
                            INSERT INTO class_teachers (teacher_id, teacher_user_id, class_id, school_id)
                            VALUES (%s, %s, %s, %s)
                        """, (found_id, found_id, int(class_id), school_id))
                    elif has_teacher_user_id:
                        # فقط teacher_user_id
                        cursor.execute("""
                            INSERT INTO class_teachers (teacher_user_id, class_id, school_id)
                            VALUES (%s, %s, %s)
                        """, (found_id, int(class_id), school_id))
                    elif has_teacher_id:
                        # فقط teacher_id
                        cursor.execute("""
                            INSERT INTO class_teachers (teacher_id, class_id, school_id)
                            VALUES (%s, %s, %s)
                        """, (found_id, int(class_id), school_id))
                    else:
                        failed_inserts.append({
                            "teacher_id": found_id,
                            "error": "لا يوجد عمود teacher_id أو teacher_user_id في الجدول"
                        })
                        continue
                    
                    added_count += 1
                    
                except Exception as insert_err:
                    failed_inserts.append({
                        "teacher_id": found_id,
                        "error": str(insert_err)
                    })
                    continue

            connection.commit()
            
            if failed_inserts and added_count == 0:
                return jsonify({
                    "status": "error",
                    "message": "فشل إدخال المعلمين",
                    "errors": failed_inserts,
                    "columns_detected": {"teacher_id": has_teacher_id, "teacher_user_id": has_teacher_user_id}
                }), 500
            
            if added_count == 0 and invalid_ids:
                return jsonify({
                    "status": "error",
                    "message": f"لم يتم العثور على المستخدمين: {', '.join(invalid_ids)}"
                }), 404
            
            if added_count == 0 and not invalid_ids:
                return jsonify({
                    "status": "success",
                    "message": "المعلمون مسجلون مسبقاً"
                }), 200
            
            msg = f"تم تسجيل {added_count} معلم بنجاح"
            if invalid_ids:
                msg += f" | لم يتم العثور على: {', '.join(invalid_ids)}"
            return jsonify({"status": "success", "message": msg}), 200
            
    except Exception as e:
        try:
            connection.rollback()
        except:
            pass
        return jsonify({"status": "error", "message": f"خطأ: {str(e)}"}), 500
    finally:
        if connection: connection.close()

@app.route('/get_connected_teachers', methods=['POST'])
def get_connected_teachers():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    if not school_id or not class_id:
        return jsonify({"status": "error", "teachers": []}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "teachers": []}), 500
    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            
            # 🆕 اكتشاف بنية الجدول
            cursor.execute("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'class_teachers'
            """)
            existing_columns = [row['column_name'] for row in cursor.fetchall()]
            has_teacher_id = 'teacher_id' in existing_columns
            has_teacher_user_id = 'teacher_user_id' in existing_columns
            
            # 🆕 اختيار العمود الصحيح
            if has_teacher_user_id:
                teacher_col = 'teacher_user_id'
            elif has_teacher_id:
                teacher_col = 'teacher_id'
            else:
                return jsonify({"status": "error", "teachers": [], "message": "no teacher column found"}), 500
            
            cursor.execute(f"""
                SELECT 
                    ct.{teacher_col} as teacher_col,
                    sp.full_name,
                    sp.image_url,
                    sp.device_id
                FROM class_teachers ct
                LEFT JOIN student_profiles sp ON sp.user_id = ct.{teacher_col}
                WHERE ct.school_id = %s AND ct.class_id = %s
                ORDER BY ct.id DESC
            """, (school_id, int(class_id)))
            rows = cursor.fetchall()
            teachers = []
            for r in rows:
                teachers.append({
                    "id": r['teacher_col'] or "",
                    "name": r['full_name'] or "معلم",
                    "device_id": r['device_id'] or "",
                    "image_url": r['image_url'] or ""
                })
            return jsonify({"status": "success", "teachers": teachers}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "teachers": []}), 500
    finally:
        if connection: connection.close()

@app.route('/join_class', methods=['POST'])
def join_class():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    device_id = request.values.get('device_id')
    user_id = request.values.get('user_id')
    if not school_id or not class_id or not device_id:
        return jsonify({"status": "error", "message": "بيانات غير مكتملة"}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            if not user_id:
                cursor.execute("SELECT user_id FROM student_profiles WHERE device_id = %s", (device_id,))
                row = cursor.fetchone()
                if row:
                    user_id = row['user_id']
            cursor.execute("""
                INSERT INTO class_presence (school_id, class_id, device_id, user_id, last_seen)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (device_id, class_id) DO UPDATE SET
                    last_seen = CURRENT_TIMESTAMP,
                    user_id = COALESCE(EXCLUDED.user_id, class_presence.user_id)
            """, (school_id, int(class_id), device_id, user_id))
            connection.commit()
            return jsonify({"status": "success", "message": "تم الانضمام"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/get_class_presence', methods=['POST'])
def get_class_presence():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    if not school_id or not class_id:
        return jsonify({"status": "error", "students": []}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "students": []}), 500
    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            cursor.execute("""
                SELECT 
                    cp.device_id,
                    cp.user_id,
                    cp.last_seen,
                    sp.full_name,
                    sp.image_url
                FROM class_presence cp
                LEFT JOIN student_profiles sp ON sp.device_id = cp.device_id
                WHERE cp.school_id = %s 
                  AND cp.class_id = %s
                  AND cp.last_seen > NOW() - INTERVAL '5 minutes'
                ORDER BY cp.last_seen DESC
            """, (school_id, int(class_id)))
            rows = cursor.fetchall()
            students = []
            for r in rows:
                students.append({
                    "device_id": r['device_id'],
                    "user_id": r['user_id'] or "",
                    "full_name": r['full_name'] or "طالب",
                    "image_url": r['image_url'] or "",
                    "last_seen": r['last_seen'].isoformat() if r['last_seen'] else ""
                })
            return jsonify({"status": "success", "students": students}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "students": []}), 500
    finally:
        if connection: connection.close()

@app.route('/heartbeat_class', methods=['POST'])
def heartbeat_class():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    device_id = request.values.get('device_id')
    if not school_id or not class_id or not device_id:
        return jsonify({"status": "error"}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                UPDATE class_presence SET last_seen = CURRENT_TIMESTAMP
                WHERE school_id = %s AND class_id = %s AND device_id = %s
            """, (school_id, int(class_id), device_id))
            connection.commit()
            return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/leave_class', methods=['POST'])
def leave_class():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    device_id = request.values.get('device_id')
    if not school_id or not class_id or not device_id:
        return jsonify({"status": "error"}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                DELETE FROM class_presence 
                WHERE school_id = %s AND class_id = %s AND device_id = %s
            """, (school_id, int(class_id), device_id))
            connection.commit()
            return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/request_mic', methods=['POST'])
def request_mic():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    device_id = request.values.get('device_id')
    user_id = request.values.get('user_id', '')
    if not school_id or not class_id or not device_id:
        return jsonify({"status": "error", "message": "بيانات غير مكتملة"}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            cursor.execute(
                "SELECT full_name, image_url FROM student_profiles WHERE device_id = %s",
                (device_id,)
            )
            student = cursor.fetchone()
            full_name = student['full_name'] if student else "طالب"
            image_url = student['image_url'] if student else ""
            cursor.execute("""
                SELECT id FROM mic_requests
                WHERE student_device_id = %s AND class_id = %s AND status = 'pending'
            """, (device_id, int(class_id)))
            existing = cursor.fetchone()
            if existing:
                return jsonify({"status": "success", "message": "لديك طلب قائم بالفعل"}), 200
            cursor.execute("""
                INSERT INTO mic_requests (school_id, class_id, student_device_id, student_user_id, student_name, student_image, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending')
            """, (school_id, int(class_id), device_id, user_id, full_name, image_url))
            connection.commit()
            return jsonify({"status": "success", "message": "تم إرسال طلب المايك"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/get_mic_requests', methods=['POST'])
def get_mic_requests():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    if not school_id or not class_id:
        return jsonify({"status": "error", "requests": []}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "requests": []}), 500
    try:
        with connection.cursor() as cursor:
            create_tables_if_not_exist(cursor)
            cursor.execute("""
                SELECT id, student_device_id, student_user_id, student_name, student_image, status, created_at
                FROM mic_requests
                WHERE school_id = %s AND class_id = %s AND status = 'pending'
                ORDER BY created_at ASC
            """, (school_id, int(class_id)))
            rows = cursor.fetchall()
            requests_list = []
            for r in rows:
                requests_list.append({
                    "id": r['id'],
                    "device_id": r['student_device_id'],
                    "user_id": r['student_user_id'] or "",
                    "name": r['student_name'] or "طالب",
                    "image_url": r['student_image'] or "",
                })
            return jsonify({"status": "success", "requests": requests_list}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "requests": []}), 500
    finally:
        if connection: connection.close()

@app.route('/respond_mic_request', methods=['POST'])
def respond_mic_request():
    school_id = request.values.get('school_id')
    request_id = request.values.get('request_id')
    action = request.values.get('action')
    if not school_id or not request_id or action not in ['approve', 'reject']:
        return jsonify({"status": "error", "message": "بيانات غير مكتملة"}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            new_status = 'approved' if action == 'approve' else 'rejected'
            cursor.execute("UPDATE mic_requests SET status = %s WHERE id = %s", (new_status, int(request_id)))
            connection.commit()
            return jsonify({"status": "success", "message": "تمت العملية"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/verify_leader_password', methods=['POST'])
def verify_leader_password():
    school_id = request.values.get('school_id')
    password = request.values.get('password')
    if not school_id or not password:
        return jsonify({"status": "error", "message": "بيانات غير مكتملة"}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT leader_password FROM schools WHERE school_id = %s", (school_id,))
            row = cursor.fetchone()
            if row and row['leader_password'] == password:
                return jsonify({"status": "success", "message": "تم التحقق بنجاح"}), 200
            return jsonify({"status": "error", "message": "كلمة سر الرئيس غير صحيحة"}), 401
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/update_leader_password', methods=['POST'])
def update_leader_password():
    school_id = request.values.get('school_id')
    current_password = request.values.get('current_password')
    new_password = request.values.get('new_password')
    if not school_id or not current_password or not new_password:
        return jsonify({"status": "error", "message": "بيانات غير مكتملة"}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT leader_password FROM schools WHERE school_id = %s", (school_id,))
            row = cursor.fetchone()
            if not row or row['leader_password'] != current_password:
                return jsonify({"status": "error", "message": "كلمة السر الحالية غير صحيحة"}), 401
            cursor.execute("UPDATE schools SET leader_password = %s WHERE school_id = %s", (new_password, school_id))
            connection.commit()
            return jsonify({"status": "success", "message": "تم تحديث سر الرئيس بنجاح"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/kick_user', methods=['POST'])
def kick_user():
    school_id = request.values.get('school_id')
    class_id = request.values.get('class_id')
    user_id = request.values.get('user_id')
    if not school_id or not class_id or not user_id:
        return jsonify({"status": "error", "message": "بيانات غير مكتملة"}), 400
    connection = get_db_connection(school_id)
    if not connection:
        return jsonify({"status": "error", "message": "خطأ في الاتصال"}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM class_presence WHERE school_id = %s AND class_id = %s AND user_id = %s", 
                          (school_id, int(class_id), user_id))
            try:
                cursor.execute("DELETE FROM class_teachers WHERE school_id = %s AND class_id = %s AND teacher_user_id = %s", 
                              (school_id, int(class_id), user_id))
            except Exception:
                try:
                    cursor.execute("DELETE FROM class_teachers WHERE school_id = %s AND class_id = %s AND teacher_id = %s", 
                                  (school_id, int(class_id), user_id))
                except Exception:
                    pass
            connection.commit()
            return jsonify({"status": "success", "message": "تم الطرد بنجاح"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "School & Classes API is running successfully!"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
