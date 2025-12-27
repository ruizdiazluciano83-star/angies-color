from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import date, datetime, time, timedelta

from db import Base, engine, SessionLocal
from models import Specialty, Staff, Client, Appointment

Base.metadata.create_all(bind=engine)

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

STUDIO_WA_NUMBER = "5491167253722"


# ---------------- DB ----------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------- MIGRACIONES SQLITE (SIN PERDER DATOS) ----------------
def ensure_sqlite_migrations():
    with engine.connect() as conn:
        # clients: last_visit
        cols = conn.execute(text("PRAGMA table_info(clients)")).fetchall()
        colnames = {c[1] for c in cols}
        if "last_visit" not in colnames:
            conn.execute(text("ALTER TABLE clients ADD COLUMN last_visit DATE"))
            conn.commit()

        # appointments: columnas nuevas
        cols2 = conn.execute(text("PRAGMA table_info(appointments)")).fetchall()
        appt_cols = {c[1] for c in cols2}

        def add_col_if_missing(col, ddl):
            nonlocal appt_cols
            if col not in appt_cols:
                conn.execute(text(ddl))
                conn.commit()
                cols_now = conn.execute(text("PRAGMA table_info(appointments)")).fetchall()
                appt_cols = {c[1] for c in cols_now}

        add_col_if_missing("wa_sent", "ALTER TABLE appointments ADD COLUMN wa_sent BOOLEAN")
        add_col_if_missing("salon", "ALTER TABLE appointments ADD COLUMN salon INTEGER")
        add_col_if_missing("deposit_paid", "ALTER TABLE appointments ADD COLUMN deposit_paid BOOLEAN")
        add_col_if_missing("deposit_amount", "ALTER TABLE appointments ADD COLUMN deposit_amount INTEGER")
        add_col_if_missing("status", "ALTER TABLE appointments ADD COLUMN status VARCHAR(30)")
        add_col_if_missing("notes", "ALTER TABLE appointments ADD COLUMN notes TEXT")
        add_col_if_missing("duration_min", "ALTER TABLE appointments ADD COLUMN duration_min INTEGER")

        # backfill defaults
        conn.execute(text("UPDATE appointments SET salon = COALESCE(salon, 1)"))
        conn.execute(text("UPDATE appointments SET wa_sent = COALESCE(wa_sent, 0)"))
        conn.execute(text("UPDATE appointments SET deposit_paid = COALESCE(deposit_paid, 0)"))
        conn.execute(text("UPDATE appointments SET deposit_amount = COALESCE(deposit_amount, 0)"))
        conn.execute(text("UPDATE appointments SET status = COALESCE(status, 'ACTIVO')"))
        conn.execute(text("UPDATE appointments SET duration_min = COALESCE(duration_min, 30)"))
        conn.commit()

        # backfill last_visit desde turnos existentes
        conn.execute(text("""
            UPDATE clients
            SET last_visit = (
                SELECT MAX(a.date)
                FROM appointments a
                WHERE a.client_id = clients.id
                  AND COALESCE(a.status,'ACTIVO') != 'CANCELADO'
            )
            WHERE last_visit IS NULL
        """))
        conn.commit()


ensure_sqlite_migrations()


# ---------------- HELPERS ----------------
def _digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def normalize_ar_phone_to_wa(phone_raw: str) -> str:
    p = _digits(phone_raw)
    if p.startswith("549") and len(p) >= 12:
        return p
    if p.startswith("54") and len(p) >= 11:
        if p.startswith("549"):
            return p
        if len(p) >= 12 and p[2:4] == "11":
            return "549" + p[2:]
        return p
    if p.startswith("11") and len(p) >= 10:
        return "549" + p
    return p


def make_wa_message(appt: Appointment) -> str:
    dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    dname = dias[appt.date.weekday()]
    dnum = appt.date.day
    hora = appt.start_time.strftime("%H:%M")
    return (
        f"Cómo estás? Quería recordarte que el día {dname}, {dnum} y {hora}, "
        f"tenemos agendado un turno. Te esperamos!! Estudio Angie´s Color-"
    )


def build_slots(start_h=8, end_h=19, step_min=30):
    slots = []
    t = datetime.combine(date.today(), time(start_h, 0))
    end = datetime.combine(date.today(), time(end_h, 0))
    while t <= end:
        slots.append(t.time())
        t += timedelta(minutes=step_min)
    return slots


SLOTS = build_slots()


def nearest_open_date(db: Session) -> date:
    today = date.today()
    appts = (
        db.query(Appointment)
        .filter(Appointment.status != "CANCELADO")
        .order_by(Appointment.date.asc())
        .all()
    )
    for a in appts:
        if a.date >= today:
            return a.date
    return today


# ---------------- HOME ----------------
@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse("/turnos", status_code=302)


# ---------------- CLIENTES ----------------
@app.get("/clientes", response_class=HTMLResponse)
def clientes(request: Request, q: str = "", db: Session = Depends(get_db)):
    qn = (q or "").strip().lower()
    all_clients = db.query(Client).order_by(Client.name.asc()).all()
    if qn:
        filtered = [c for c in all_clients if qn in (c.name or "").lower() or qn in (c.phone or "")]
    else:
        filtered = all_clients

    return templates.TemplateResponse("clientes.html", {"request": request, "clients": filtered, "q": q})


@app.post("/clientes/nuevo")
def nuevo_cliente(
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    c = Client(name=name.strip(), phone=phone.strip(), email=email.strip(), notes=notes.strip())
    db.add(c)
    db.commit()
    return RedirectResponse("/clientes", status_code=303)


@app.get("/clientes/{client_id}", response_class=HTMLResponse)
def cliente_ficha(request: Request, client_id: int, db: Session = Depends(get_db)):
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Not Found")

    history = (
        db.query(Appointment)
        .filter(Appointment.client_id == client_id, Appointment.status != "CANCELADO")
        .order_by(Appointment.date.desc(), Appointment.start_time.desc())
        .all()
    )

    last = c.last_visit.strftime("%d/%m/%Y") if getattr(c, "last_visit", None) else "—"

    return templates.TemplateResponse("cliente_ficha.html", {"request": request, "c": c, "last": last, "history": history})


@app.post("/clientes/{client_id}/editar")
def cliente_editar(
    client_id: int,
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Not Found")

    c.name = name.strip()
    c.phone = phone.strip()
    c.email = email.strip()
    c.notes = notes.strip()
    db.commit()

    return RedirectResponse(f"/clientes/{client_id}", status_code=303)


# ---------------- ADMIN ----------------
@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, db: Session = Depends(get_db)):
    specialties = db.query(Specialty).order_by(Specialty.name.asc()).all()
    staff = db.query(Staff).order_by(Staff.name.asc()).all()
    return templates.TemplateResponse("admin.html", {"request": request, "specialties": specialties, "staff": staff})


@app.post("/admin/especialidades/nueva")
def nueva_especialidad(
    name: str = Form(...),
    color_hex: str = Form("#60a5fa"),
    db: Session = Depends(get_db),
):
    db.add(Specialty(name=name.strip().upper(), color_hex=color_hex.strip()))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/staff/nuevo")
def nuevo_staff(name: str = Form(...), db: Session = Depends(get_db)):
    db.add(Staff(name=name.strip().upper()))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/especialidades/{sid}/eliminar")
@app.post("/admin/especialidades/{sid}/borrar")
def borrar_especialidad(sid: int, db: Session = Depends(get_db)):
    sp = db.query(Specialty).filter(Specialty.id == sid).first()
    if not sp:
        raise HTTPException(status_code=404, detail="Not Found")
    db.delete(sp)
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/staff/{sid}/eliminar")
@app.post("/admin/staff/{sid}/borrar")
def borrar_staff(sid: int, db: Session = Depends(get_db)):
    st = db.query(Staff).filter(Staff.id == sid).first()
    if not st:
        raise HTTPException(status_code=404, detail="Not Found")
    db.delete(st)
    db.commit()
    return RedirectResponse("/admin", status_code=303)


# ---------------- TURNOS ----------------
@app.get("/turnos", response_class=HTMLResponse)
def turnos(
    request: Request,
    date_str: str = "",
    staff_id: int = 0,
    salon: int = 1,
    db: Session = Depends(get_db),
):
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except:
            selected_date = nearest_open_date(db)
    else:
        selected_date = nearest_open_date(db)

    staffs = db.query(Staff).order_by(Staff.name.asc()).all()
    if staff_id == 0 and staffs:
        staff_id = staffs[0].id

    if salon not in (1, 2):
        salon = 1

    q = (
        db.query(Appointment)
        .filter(
            Appointment.date == selected_date,
            Appointment.status != "CANCELADO",
            Appointment.salon == salon,
        )
    )
    if staff_id:
        q = q.filter(Appointment.staff_id == staff_id)

    day_appts = q.order_by(Appointment.start_time.asc()).all()
    by_time = {a.start_time.strftime("%H:%M"): a for a in day_appts}

    open_dates_rows = db.query(Appointment.date).filter(Appointment.status != "CANCELADO").distinct().all()
    days_with_turnos = sorted({d[0].strftime("%Y-%m-%d") for d in open_dates_rows})

    weekday_names = ["LUNES","MARTES","MIÉRCOLES","JUEVES","VIERNES","SÁBADO","DOMINGO"]

    return templates.TemplateResponse(
        "turnos.html",
        {
            "request": request,
            "selected_date": selected_date.strftime("%Y-%m-%d"),
            "selected_date_label": selected_date.strftime("%d/%m/%Y"),
            "selected_weekday": weekday_names[selected_date.weekday()],
            "slots": [t.strftime("%H:%M") for t in SLOTS],
            "by_time": by_time,
            "staffs": staffs,
            "staff_id": staff_id,
            "salon": salon,
            "days_with_turnos": days_with_turnos,
        },
    )


@app.get("/turnos/nuevo", response_class=HTMLResponse)
def turnos_nuevo(
    request: Request,
    date_str: str = "",
    time_str: str = "",
    staff_id: int = 0,
    salon: int = 1,
    db: Session = Depends(get_db),
):
    clients = db.query(Client).order_by(Client.name.asc()).all()
    specialties = db.query(Specialty).order_by(Specialty.name.asc()).all()
    staffs = db.query(Staff).order_by(Staff.name.asc()).all()

    if salon not in (1, 2):
        salon = 1

    return templates.TemplateResponse(
        "turnos_nuevo.html",
        {
            "request": request,

            # listas + alias (por compatibilidad con templates)
            "clients": clients,
            "clientes": clients,

            "specialties": specialties,
            "especialidades": specialties,

            "staff": staffs,
            "staffs": staffs,
            "staff_list": staffs,

            # valores
            "date_str": date_str,
            "time_str": time_str,
            "staff_id": staff_id,
            "salon": salon,
        },
    )


@app.post("/turnos/nuevo")
def turnos_crear(
    date_str: str = Form(...),
    time_str: str = Form(...),
    client_id: int = Form(0),
    new_name: str = Form(""),
    new_phone: str = Form(""),
    specialty_id: int = Form(0),
    duration_min: int = Form(30),
    staff_id: int = Form(0),
    salon: int = Form(1),
    deposit_paid: str = Form("0"),
    deposit_amount: int = Form(0),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        t = datetime.strptime(time_str, "%H:%M").time()
    except:
        raise HTTPException(status_code=400, detail="Fecha u hora inválida")

    if client_id and client_id > 0:
        client = db.query(Client).filter(Client.id == client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Cliente no existe")
    else:
        if not new_name.strip():
            raise HTTPException(status_code=400, detail="Falta nombre del cliente")
        client = Client(name=new_name.strip(), phone=new_phone.strip())
        db.add(client)
        db.commit()
        db.refresh(client)

    appt = Appointment(
        date=d,
        start_time=t,
        duration_min=int(duration_min),
        client_id=client.id,
        specialty_id=(specialty_id if specialty_id > 0 else None),
        staff_id=(staff_id if staff_id > 0 else None),
        salon=(salon if salon in (1, 2) else 1),
        deposit_paid=(deposit_paid == "1"),
        deposit_amount=(int(deposit_amount) if deposit_paid == "1" else 0),
        notes=notes.strip(),
        status="ACTIVO",
        wa_sent=False,
    )
    db.add(appt)

    # last_visit
    try:
        client.last_visit = d
    except:
        pass

    db.commit()

    return RedirectResponse(
        f"/turnos?date_str={date_str}&staff_id={staff_id}&salon={appt.salon}",
        status_code=303,
    )


# ✅ EDITAR TURNO (ANTI-CRASH POR VARIABLES)
@app.get("/turnos/{appt_id}/editar", response_class=HTMLResponse)
def turno_editar(request: Request, appt_id: int, db: Session = Depends(get_db)):
    appt = db.query(Appointment).filter(Appointment.id == appt_id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Not Found")

    clients = db.query(Client).order_by(Client.name.asc()).all()
    specialties = db.query(Specialty).order_by(Specialty.name.asc()).all()
    staffs = db.query(Staff).order_by(Staff.name.asc()).all()

    # Alias: turno_editar.html puede esperar cualquier nombre
    return templates.TemplateResponse(
        "turno_editar.html",
        {
            "request": request,

            # el turno con aliases
            "appt": appt,
            "appointment": appt,
            "turno": appt,

            # listas con aliases
            "clients": clients,
            "clientes": clients,

            "specialties": specialties,
            "especialidades": specialties,

            "staff": staffs,
            "staffs": staffs,
            "staff_list": staffs,
        },
    )


@app.post("/turnos/{appt_id}/wa_sent")
def mark_wa_sent(appt_id: int, db: Session = Depends(get_db)):
    appt = db.query(Appointment).filter(Appointment.id == appt_id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Not Found")
    appt.wa_sent = True
    db.commit()
    return JSONResponse({"ok": True})


@app.get("/turnos/{appt_id}/wa_link")
def get_wa_link(appt_id: int, db: Session = Depends(get_db)):
    appt = db.query(Appointment).filter(Appointment.id == appt_id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Not Found")

    if not appt.client or not appt.client.phone:
        raise HTTPException(status_code=400, detail="Cliente sin teléfono")

    phone = normalize_ar_phone_to_wa(appt.client.phone)
    msg = make_wa_message(appt)

    import urllib.parse
    url = f"https://wa.me/{phone}?text={urllib.parse.quote(msg)}"
    return JSONResponse({"url": url})
