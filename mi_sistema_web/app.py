from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, g, make_response, send_file
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
from io import StringIO
import csv

app = Flask(__name__)
app.secret_key = 'clave_secreta_segura'
DATABASE = 'inventario_ventas.db'

# --- Decorador para verificar sesión y evitar caché ---
def login_requerido(f):
    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario' not in session:
            return redirect(url_for('login'))
        response = make_response(f(*args, **kwargs))
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        return response
    return decorada

# --- Conexión a BD ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db:
        db.close()

# --- LOGIN ---
@app.route('/')
def login():
    return render_template('login.html')

@app.route('/auth', methods=['POST'])
def auth():
    usuario = request.form['usuario']
    clave = request.form['clave']

    cur = get_db().cursor()
    cur.execute("SELECT * FROM usuarios WHERE usuario=? AND contraseña=?", (usuario, clave))
    user = cur.fetchone()

    if user:
        session['usuario'] = user[1]
        session['rol'] = user[3]
        return redirect(url_for('dashboard'))
    return render_template('login.html', error='Usuario o contraseña incorrectos')

# --- FUNCIONES AUXILIARES ---
def obtener_productos():
    cur = get_db().cursor()
    cur.execute("SELECT id, nombre FROM productos")
    return cur.fetchall()

# --- DASHBOARD ---
@app.route('/dashboard')
@login_requerido
def dashboard():
    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT COUNT(*) FROM productos")
    total_productos = cur.fetchone()[0]

    cur.execute("SELECT SUM(total) FROM ventas WHERE DATE(fecha) = DATE('now')")
    ventas_dia = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM ventas WHERE DATE(fecha) = DATE('now')")
    transacciones_dia = cur.fetchone()[0]

    cur.execute("SELECT id, precio_compra, precio_unidad, precio_six FROM productos")
    productos = cur.fetchall()

    ganancia_real = 0
    for producto in productos:
        producto_id, precio_compra, precio_unidad, precio_six = producto
        if not precio_compra:
            continue
        costo_unitario = precio_compra / 24
        ganancia_u = precio_unidad - costo_unitario
        ganancia_s = precio_six - (costo_unitario * 6)

        cur.execute("SELECT SUM(cantidad) FROM ventas WHERE producto_id=? AND tipo_venta='unidad'", (producto_id,))
        ventas_u = cur.fetchone()[0] or 0

        cur.execute("SELECT SUM(cantidad) FROM ventas WHERE producto_id=? AND tipo_venta='six'", (producto_id,))
        ventas_s = cur.fetchone()[0] or 0

        ganancia_producto = (ganancia_u * ventas_u) + (ganancia_s * ventas_s)
        ganancia_real += ganancia_producto

    cur.execute("SELECT nombre, six_disponibles, unidades_sueltas FROM productos WHERE six_disponibles <= 3 OR unidades_sueltas <= 5")
    alertas = cur.fetchall()

    hoy = datetime.now().date()
    ventas_por_dia = defaultdict(float)
    for i in range(7):
        dia = hoy - timedelta(days=i)
        cur.execute("SELECT SUM(total) FROM ventas WHERE DATE(fecha) = ?", (dia.isoformat(),))
        total_dia = cur.fetchone()[0] or 0
        ventas_por_dia[dia.strftime('%Y-%m-%d')] = total_dia

    fechas_ordenadas = sorted(ventas_por_dia.keys())
    valores_ordenados = [ventas_por_dia[fecha] for fecha in fechas_ordenadas]

    mensaje_toast = session.pop('mensaje_toast', None)

    response = make_response(render_template('dashboard.html',
                                             usuario=session['usuario'],
                                             rol=session['rol'],
                                             total_productos=total_productos,
                                             ventas_dia=ventas_dia,
                                             transacciones_dia=transacciones_dia,
                                             ganancia_estimada=round(ganancia_real, 2),
                                             alertas=alertas,
                                             fechas_ventas=fechas_ordenadas,
                                             montos_ventas=valores_ordenados,
                                             mensaje_toast=mensaje_toast))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response

# --- INVENTARIO ---
@app.route('/inventario')
@login_requerido
def inventario():
    cur = get_db().cursor()
    cur.execute("SELECT * FROM productos")
    productos_raw = cur.fetchall()

    productos = []
    for p in productos_raw:
        (id, nombre, tipo, six_disponibles, unidades_sueltas, precio_six, precio_unidad, precio_compra, _) = p
        if precio_compra:  # Corregido el nombre de la variable
            costo_unitario = precio_compra / 24
            ganancia = ((precio_unidad - costo_unitario) * unidades_sueltas) + ((precio_six - (costo_unitario * 6)) * six_disponibles)
        else:
            ganancia = 0
        
        # Convertimos a diccionario para que Jinja2 acceda con .nombre, .tipo, etc.
        productos.append({
            "id": id,
            "nombre": nombre,
            "tipo": tipo,
            "six": six_disponibles,
            "unidades": unidades_sueltas,
            "precio_six": precio_six,
            "precio_unidad": precio_unidad,
            "precio_compra": precio_compra,
            "ganancia": round(ganancia, 2)  # Nombre coincidente con el HTML
        })

    return render_template('inventario.html', productos=productos, rol=session['rol'])

@app.route('/exportar_inventario')
@login_requerido
def exportar_inventario():
    cur = get_db().cursor()
    cur.execute("SELECT nombre, tipo, six_disponibles, unidades_sueltas, precio_six, precio_unidad, precio_compra FROM productos")
    productos = cur.fetchall()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['Nombre', 'Tipo', 'Six Disponibles', 'Unidades Sueltas', 'Precio Six', 'Precio Unidad', 'Precio Compra'])
    for p in productos:
        writer.writerow(p)

    output.seek(0)
    return send_file(output, mimetype='text/csv', as_attachment=True, download_name='inventario.csv')

# --- FACTURAR ---
@app.route('/facturar', methods=['GET', 'POST'])
@login_requerido
def facturar():
    db = get_db()
    cur = db.cursor()

    if request.method == 'POST':
        producto_id = request.form['producto_id']
        cantidad = int(request.form['cantidad'])
        tipo_venta = request.form['tipo_venta']
        pagado = float(request.form['pagado'])
        metodo_pago = request.form['metodo_pago']  # NEW: Captura el método de pago del formulario

        cur.execute("SELECT nombre, precio_six, precio_unidad FROM productos WHERE id=?", (producto_id,))
        producto = cur.fetchone()
        total = cantidad * (producto[1] if tipo_venta == 'six' else producto[2])
        cambio = pagado - total

        # NEW: Añade 'metodo_pago' al INSERT
        cur.execute("""
            INSERT INTO ventas (producto_id, cantidad, tipo_venta, total, fecha, vendedor, metodo_pago) 
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (producto_id, cantidad, tipo_venta, total, 
             datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 
             session['usuario'], metodo_pago)  # NEW: Añade el método de pago
        )

        if tipo_venta == 'six':
            cur.execute("UPDATE productos SET six_disponibles = six_disponibles - ? WHERE id=?", (cantidad, producto_id))
        else:
            cur.execute("UPDATE productos SET unidades_sueltas = unidades_sueltas - ? WHERE id=?", (cantidad, producto_id))

        db.commit()
        return render_template('facturacion.html', productos=obtener_productos(), cambio=cambio)

    return render_template('facturacion.html', productos=obtener_productos())

@app.route('/ventas_por_dia', methods=['GET', 'POST'])
@login_requerido
def ventas_por_dia():
    db = get_db()
    cur = db.cursor()
    
    # Consulta base para obtener todas las ventas
    query = """
        SELECT 
            v.id, 
            p.nombre, 
            v.cantidad, 
            v.tipo_venta, 
            v.total, 
            v.fecha, 
            v.vendedor, 
            v.metodo_pago
        FROM ventas v
        JOIN productos p ON v.producto_id = p.id
    """
    
    # Si se envió el formulario de filtrado por fecha
    if request.method == 'POST' and 'fecha' in request.form:
        fecha = request.form['fecha']
        query += " WHERE DATE(v.fecha) = ?"
        cur.execute(query, (fecha,))
    else:
        # Consulta sin filtro (todas las ventas)
        query += " ORDER BY v.fecha DESC"
        cur.execute(query)
    
    ventas = cur.fetchall()
    
    return render_template('ventas_por_dia.html', 
                         resumen=ventas,
                         rol=session['rol'])

# --- AGREGAR PRODUCTO ---
@app.route('/agregar_producto', methods=['GET', 'POST'])
@login_requerido
def agregar_producto():
    if session['rol'] != 'admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        nombre = request.form['nombre']
        tipo = request.form['tipo']
        precio_six = float(request.form['precio_six'])
        precio_unidad = float(request.form['precio_unidad'])
        precio_compra = float(request.form['precio_compra'])
        six_disponibles = int(request.form['six_disponibles'])
        unidades_sueltas = int(request.form['unidades_sueltas'])

        costo_unitario = precio_compra / 24
        ganancia_estimada = ((precio_unidad - costo_unitario) * unidades_sueltas) + \
                            ((precio_six - (costo_unitario * 6)) * six_disponibles)

        cur = get_db().cursor()
        cur.execute('''
            INSERT INTO productos (nombre, tipo, six_disponibles, unidades_sueltas, precio_six, precio_unidad, precio_compra, ganancia_estimada)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (nombre, tipo, six_disponibles, unidades_sueltas, precio_six, precio_unidad, precio_compra, ganancia_estimada))
        get_db().commit()

        return redirect(url_for('inventario'))

    return render_template('agregar_producto.html')
    

@app.route('/eliminar_producto/<int:id>')
@login_requerido
def eliminar_producto(id):
    if session['rol'] != 'admin':
        return redirect(url_for('login'))

    cur = get_db().cursor()
    cur.execute("DELETE FROM productos WHERE id = ?", (id,))
    get_db().commit()
    return redirect(url_for('inventario'))
@app.route('/editar_producto/<int:id>', methods=['GET', 'POST'])
@login_requerido
def editar_producto(id):
    if session['rol'] != 'admin':
        return redirect(url_for('login'))

    cur = get_db().cursor()
    
    if request.method == 'POST':
        # Recupera los datos del formulario (usando request.form)
        nombre = request.form['nombre']
        tipo = request.form['tipo']
        six_disponibles = request.form['six_disponibles']
        unidades_sueltas = request.form['unidades_sueltas']
        precio_six = request.form['precio_six']
        precio_unidad = request.form['precio_unidad']
        precio_compra = request.form.get('precio_compra')  # Usamos get() para evitar KeyError si es opcional

        # Actualiza el producto en la base de datos
        cur.execute("""
            UPDATE productos 
            SET nombre = ?, tipo = ?, six_disponibles = ?, unidades_sueltas = ?,
                precio_six = ?, precio_unidad = ?, precio_compra = ?
            WHERE id = ?
        """, (nombre, tipo, six_disponibles, unidades_sueltas, precio_six, precio_unidad, precio_compra, id))
        
        get_db().commit()
        return redirect(url_for('inventario'))

    # Si es GET, muestra el formulario con los datos actuales
    cur.execute("SELECT * FROM productos WHERE id = ?", (id,))
    producto = cur.fetchone()
    
    if not producto:
        return "Producto no encontrado", 404

    # Pasa los datos a la plantilla (sin cambiar nombres de variables)
    return render_template('editar_producto.html', producto=producto)
# --- VENTAS POR DÍA ---

@app.route('/eliminar_venta/<int:id>', methods=["POST"])
@login_requerido
def eliminar_venta(id):
    if session.get('rol') != 'admin':
        return redirect(url_for('ventas_por_dia'))

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT producto_id, cantidad, tipo_venta FROM ventas WHERE id = ?", (id,))
    venta = cursor.fetchone()

    if venta:
        producto_id, cantidad, tipo_venta = venta
        if tipo_venta == 'unidad':
            cursor.execute("UPDATE productos SET unidades_sueltas = unidades_sueltas + ? WHERE id = ?", (cantidad, producto_id))
        elif tipo_venta == 'six':
            cursor.execute("UPDATE productos SET six_disponibles = six_disponibles + ? WHERE id = ?", (cantidad, producto_id))

        cursor.execute("DELETE FROM ventas WHERE id = ?", (id,))
        db.commit()

    return redirect(url_for('ventas_por_dia'))

# --- GANANCIAS ---
@app.route('/ganancias')
@login_requerido
def ganancias():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, nombre, precio_compra, precio_unidad, precio_six FROM productos")
    productos = cur.fetchall()

    resumen = []
    total_ganancia = 0

    for producto in productos:
        producto_id, nombre, precio_compra, precio_unidad, precio_six = producto
        costo_unitario = precio_compra / 24
        ganancia_unidad = precio_unidad - costo_unitario
        ganancia_six = precio_six - (costo_unitario * 6)

        cur.execute("SELECT SUM(cantidad) FROM ventas WHERE producto_id=? AND tipo_venta='unidad'", (producto_id,))
        ventas_unidades = cur.fetchone()[0] or 0

        cur.execute("SELECT SUM(cantidad) FROM ventas WHERE producto_id=? AND tipo_venta='six'", (producto_id,))
        ventas_six = cur.fetchone()[0] or 0

        total_producto = (ventas_unidades * ganancia_unidad) + (ventas_six * ganancia_six)
        total_ganancia += total_producto

        resumen.append({
            'nombre': nombre,
            'ventas_unidades': ventas_unidades,
            'ventas_six': ventas_six,
            'ganancia_unidad': round(ganancia_unidad, 2),
            'ganancia_six': round(ganancia_six, 2),
            'total_producto': round(total_producto, 2)
        })

    return render_template('ganancias.html', resumen=resumen, total_ganancia=round(total_ganancia, 2))

# --- LOGOUT ---
@app.route('/logout')
def logout():
    session.clear()
    response = make_response(redirect(url_for('login')))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response

# --- MAIN ---
if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

