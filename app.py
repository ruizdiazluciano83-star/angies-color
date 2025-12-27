from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text, inspect
from datetime import datetime, date, time
from urllib.parse import quote

from db import Base, engine, SessionLocal
from models import Specialty, Staff, Salon, Client, Appointment, ClientNote

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------- DB helpers ----------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema():
    """
    Auto-upgrade básico (sin borrar datos):
    - crea tablas nuevas
    - agrega columnas nuevas si faltan (SQLite / Postgres)
    """
    Base.metadata.create_all(bind=engine)

    insp = inspect(engine)

    # --- asegurar tabla salons ---
    if "salons" not in insp.get_table_names():
        Salon.__table__.create(bind=engine)

    # --- asegurar columnas en clients ---
    if "clients" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("clients")}
        if "last_visit_date" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE clients ADD COLUMN last_visit_date DATE"))

    # --- asegurar columnas en appointments ---
    if "appointments" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("appointments")}
        # columnas nuevas históricas
        if "reminder_sent" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE appointments ADD COLUMN reminder_sent BOOLEAN DEFAULT 0"))
        if "reminder_sent_at" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE appointments ADD COLUMN reminder_sent_at DATETIME"))
        # staff/salon obligatorios: si venís de versión vieja, puede existir staff_id nullable o salon_id no existir
        if "salon_id" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE appointments ADD COLUMN salon_id INTEGER"))
        if "staff_id" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE appointments ADD COLUMN staff_id INTEGER"))

    # seed salones si faltan
    with SessionLocal() as db:
        if db.query(Salon).count() == 0:
            db.add_all([Salon(name="Salón 1"), Salon(name="Salón 2")])
            db.commit()


ensure_schema()


# ---------- constants ----------
STUDIO_NAME = "Angie´s Color"
STUDIO_WA_NUMBER = "5491167253722"  # no se usa para mandar, solo referencia


# ---------- utils ----------
def normalize_ar_whatsapp(phone_raw: str) -> str:
    if not phone_raw:
        return ""
    digits = "".join(ch for ch in phone_raw if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = digits[1:]
    if digits.startswith("11") and len(digits) >= 4 and digits[2:4] == "15":
        digits = "11" + digits[4:]
    if digits.startswith("549") and len(digits) >= 12:
        return digits
    if digits.startswith("54"):
        rest = digits[2:]
        if rest.startswith("9"):
            return digits
        return "549" + rest
    if digits.startswith("9"):
        return "54" + digits
    if digits.startswith("11") and len(digits) == 10:
        return "549" + digits
    if len(digits) >= 10:
        return "549" + digits if not digits.startswith("549") else digits
    return ""


def day_name_es(d: date) -> str:
    names = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    return names[d.weekday()]


def to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def minutes_to_str(m: int) -> str:
    hh = m // 60
    mm = m % 60
    return f"{hh:02d}:{mm:02d}"


def overlaps(a_start, a_dur, b_start, b_dur) -> bool:
    a_end = a_start + a_dur
    b_end = b_start + b_dur
    return not (a_end <= b_start or b_end <= a_start)


def validate_no_overlap(db: Session, appt_date: date, start_t: time, dur: int, staff_id: int, exclude_id: int | None = None) -> bool:
    """
    Solapamiento SOLO por staff (así hay simultáneos entre staff).
    """
    start_m = to_minutes(start_t)
    existing = (
        db.query(Appointment)
        .filter(Appointment.date == appt_date, Appointment.status != "CANCELADO", Appointment.staff_id == staff_id)
        .all()
    )
    for a in existing:
        if exclude_id and a.id == exclude_id:
            continue
        a_start = to_minutes(a.start_time)
        if overlaps(start_m, dur, a_start, a.duration_min):
            return False
    return True


def pick_default_day(db: Session) -> date:
    """
    Mostrar el día más próximo con turnos (verde).
    Si no hay futuros, mostrar hoy.
    """
    today = date.today()
    next_day = (
        db.query(Appointment.date)
        .filter(Appointment.date >= today, Appointment.status != "CANCELADO")
        .order_by(Appointment.date.asc())
        .first()
    )
    return next_day[0] if next_day else today


def build_day_grid(db: Session, appt_date: date, staff_id: int):
    day_start = 8 * 60
    day_end = 19 * 60
    step = 30

    appts = (
        db.query(Appointment)
        .filter(
            Appointment.date == appt_date,
            Appointment.status != "CANCELADO",
            Appointment.staff_id == staff_id,
        )
        .order_by(Appointment.start_time.asc())
        .all()
    )

    occupied = {}
    blocked = set()
    for a in appts:
        s = to_minutes(a.start_time)
        occupied[s] = a
        blocks = (a.duration_min // step) - 1
        for i in range(1, blocks + 1):
            blocked.add(s + i * step)

    grid = []
    for m in range(day_start, day_end + 1, step):
        if m in occupied:
            a = occupied[m]
            grid.append({
                "time": minutes_to_str(m),
                "state": "occupied",
                "id": a.id,
                "client": a.client.name,
                "specialty": a.specialty.name,
                "color": a.specialty.color_hex,
                "deposit_paid": bool(a.deposit_paid),
                "deposit_amount": a.deposit_amount or 0,
                "duration_min": a.duration_min,
                "reminder_sent": bool(a.reminder_sent),
                "salon": a.salon.name if a.salon else "",
            })
        elif m in blocked:
            grid.append({"time": minutes_to_str(m), "state": "blocked"})
        else:
            grid.append({"time": minutes_to_str(m), "state": "free"})
    return grid


# ---------- routes ----------
@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse("/turnos", status_code=302)


@app.get("/turnos", response_class=HTMLResponse)
def turnos(request: Request, date_str: str = "", staff_id: int = 0, db: Session = Depends(get_db)):
    # staff list
    staff_list = db.query(Staff).order_by(Staff.name.asc()).all()

    # si no viene staff_id, usar el primero
    if staff_id == 0 and staff_list:
        staff_id = staff_list[0].id

    # elegir día
    selected = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else pick_default_day(db)

    # días con turnos (para puntos verdes)
    dates = (
        db.query(Appointment.date)
        .filter(Appointment.status != "CANCELADO")
        .distinct()
        .order_by(Appointment.date.asc())
        .all()
    )
    days_with_turns = [d[0].strftime("%Y-%m-%d") for d in dates]

    grid = build_day_grid(db, selected, staff_id) if staff_id else []

    return templates.TemplateResponse(
        "turnos.html",
        {
            "request": request,
            "selected_date": selected.strftime("%Y-%m-%d"),
            "days_with_turns": days_with_turns,
            "grid": grid,
            "staff_list": staff_list,
            "active_staff_id": staff_id,
        },
    )


@app.get("/turnos/{appt_id}/whatsapp")
def whatsapp_recordatorio(appt_id: int, db: Session = Depends(get_db)):
    appt = db.query(Appointment).filter(Appointment.id == appt_id).first()
    if not appt:
        return RedirectResponse("/turnos", status_code=302)

    wa_to = normalize_ar_whatsapp(appt.client.phone or "")
    day_str = appt.date.strftime("%Y-%m-%d")
    if not wa_to:
        return RedirectResponse(f"/turnos?date_str={day_str}&staff_id={appt.staff_id}", status_code=303)

    dia = day_name_es(appt.date)
    nro_dia = appt.date.day
    hora = appt.start_time.strftime("%H:%M")

    msg = f"Cómo estás? Quería recordarte que el día {dia}, {nro_dia} y {hora}, tenemos agendado un turno. Te esperamos!! Estudio {STUDIO_NAME}-"
    url = f"https://wa.me/{wa_to}?text={quote(msg)}"

    appt.reminder_sent = True
    appt.reminder_sent_at = datetime.utcnow()
    db.commit()

    return RedirectResponse(url, status_code=302)


@app.get("/turnos/nuevo", response_class=HTMLResponse)
def turnos_nuevo(request: Request, date_str: str = "", time_str: str = "", staff_id: int = 0, db: Session = Depends(get_db)):
    clients = db.query(Client).order_by(Client.name.asc()).all()
    specialties = db.query(Specialty).order_by(Specialty.name.asc()).all()
    staff_list = db.query(Staff).order_by(Staff.name.asc()).all()
    salons = db.query(Salon).order_by(Salon.name.asc()).all()

    if staff_id == 0 and staff_list:
        staff_id = staff_list[0].id

    return templates.TemplateResponse(
        "turnos_nuevo.html",
        {
            "request": request,
            "clients": clients,
            "specialties": specialties,
            "staff_list": staff_list,
            "salons": salons,
            "pref_date": date_str,
            "pref_time": time_str,
            "pref_staff_id": staff_id,
            "error": request.query_params.get("error", ""),
        },
    )


@app.post("/turnos/nuevo")
def turnos_nuevo_post(
    fecha: str = Form(...),
    hora: str = Form(...),
    duration_min: int = Form(...),
    specialty_id: int = Form(...),

    staff_id: int = Form(...),
    salon_id: int = Form(...),

    client_mode: str = Form("existing"),
    client_id: int = Form(0),
    new_client_name: str = Form(""),
    new_client_phone: str = Form(""),

    deposit_paid: str = Form("off"),
    deposit_amount: int = Form(0),

    db: Session = Depends(get_db),
):
    # cliente
    if client_mode == "new":
        if not new_client_name.strip():
            return RedirectResponse("/turnos/nuevo?error=Falta+nombre+de+cliente", status_code=303)
        c = Client(name=new_client_name.strip(), phone=new_client_phone.strip())
        db.add(c)
        db.commit()
        db.refresh(c)
        final_client_id = c.id
    else:
        final_client_id = int(client_id)

    appt_date = datetime.strptime(fecha, "%Y-%m-%d").date()
    appt_time = datetime.strptime(hora, "%H:%M").time()

    if not validate_no_overlap(db, appt_date, appt_time, int(duration_min), int(staff_id)):
        return RedirectResponse(f"/turnos/nuevo?date_str={fecha}&time_str={hora}&staff_id={staff_id}&error=Horario+ocupado", status_code=303)

    paid = (deposit_paid == "on")
    amount = int(deposit_amount) if paid else 0

    appt = Appointment(
        date=appt_date,
        start_time=appt_time,
        duration_min=int(duration_min),
        client_id=final_client_id,
        specialty_id=int(specialty_id),
        staff_id=int(staff_id),
        salon_id=int(salon_id),
        status="CONFIRMADO",
        deposit_paid=paid,
        deposit_amount=amount,
        reminder_sent=False,
        reminder_sent_at=None,
    )
    db.add(appt)

    # actualizar última visita
    client = db.query(Client).filter(Client.id == final_client_id).first()
    if client:
        if (client.last_visit_date is None) or (appt_date >= client.last_visit_date):
            client.last_visit_date = appt_date

    db.commit()
    return RedirectResponse(f"/turnos?date_str={fecha}&staff_id={staff_id}", status_code=303)


@app.get("/turnos/{appt_id}/editar", response_class=HTMLResponse)
def turno_editar(appt_id: int, request: Request, db: Session = Depends(get_db)):
    appt = db.query(Appointment).filter(Appointment.id == appt_id).first()
    if not appt:
        return RedirectResponse("/turnos", status_code=302)

    clients = db.query(Client).order_by(Client.name.asc()).all()
    specialties = db.query(Specialty).order_by(Specialty.name.asc()).all()
    staff_list = db.query(Staff).order_by(Staff.name.asc()).all()
    salons = db.query(Salon).order_by(Salon.name.asc()).all()

    return templates.TemplateResponse(
        "turno_editar.html",
        {
            "request": request,
            "appt": appt,
            "clients": clients,
            "specialties": specialties,
            "staff_list": staff_list,
            "salons": salons,
            "error": request.query_params.get("error", ""),
        },
    )


@app.post("/turnos/{appt_id}/editar")
def turno_editar_post(
    appt_id: int,
    fecha: str = Form(...),
    hora: str = Form(...),
    duration_min: int = Form(...),
    specialty_id: int = Form(...),
    client_id: int = Form(...),
    staff_id: int = Form(...),
    salon_id: int = Form(...),
    deposit_paid: str = Form("off"),
    deposit_amount: int = Form(0),
    status: str = Form("CONFIRMADO"),
    db: Session = Depends(get_db),
):
    appt = db.query(Appointment).filter(Appointment.id == appt_id).first()
    if not appt:
        return RedirectResponse("/turnos", status_code=302)

    appt_date = datetime.strptime(fecha, "%Y-%m-%d").date()
    appt_time = datetime.strptime(hora, "%H:%M").time()

    if not validate_no_overlap(db, appt_date, appt_time, int(duration_min), int(staff_id), exclude_id=appt_id):
        return RedirectResponse(f"/turnos/{appt_id}/editar?error=Horario+ocupado", status_code=303)

    appt.date = appt_date
    appt.start_time = appt_time
    appt.duration_min = int(duration_min)
    appt.specialty_id = int(specialty_id)
    appt.client_id = int(client_id)
    appt.staff_id = int(staff_id)
    appt.salon_id = int(salon_id)
    appt.status = status

    paid = (deposit_paid == "on")
    appt.deposit_paid = paid
    appt.deposit_amount = int(deposit_amount) if paid else 0

    # actualizar última visita
    client = db.query(Client).filter(Client.id == int(client_id)).first()
    if client:
        if (client.last_visit_date is None) or (appt_date >= client.last_visit_date):
            client.last_visit_date = appt_date

    db.commit()
    return RedirectResponse(f"/turnos?date_str={fecha}&staff_id={staff_id}", status_code=303)


@app.post("/turnos/{appt_id}/eliminar")
def turno_eliminar(appt_id: int, db: Session = Depends(get_db)):
    appt = db.query(Appointment).filter(Appointment.id == appt_id).first()
    if appt:
        staff_id = appt.staff_id
        d = appt.date.strftime("%Y-%m-%d")
        db.delete(appt)
        db.commit()
        return RedirectResponse(f"/turnos?date_str={d}&staff_id={staff_id}", status_code=303)
    return RedirectResponse("/turnos", status_code=303)


# ---------- CLIENTES ----------
@app.get("/clientes", response_class=HTMLResponse)
def clientes(request: Request, q: str = "", db: Session = Depends(get_db)):
    query = db.query(Client)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter((Client.name.ilike(like)) | (Client.phone.ilike(like)))
    clients = query.order_by(Client.name.asc()).all()
    return templates.TemplateResponse("clientes.html", {"request": request, "clients": clients, "q": q})


@app.get("/clientes/{client_id}", response_class=HTMLResponse)
def cliente_ficha(client_id: int, request: Request, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        return RedirectResponse("/clientes", status_code=302)

    notes = (
        db.query(ClientNote)
        .filter(ClientNote.client_id == client_id)
        .order_by(ClientNote.created_at.desc())
        .all()
    )

    return templates.TemplateResponse(
        "cliente_ficha.html",
        {"request": request, "client": client, "notes": notes, "error": request.query_params.get("error", "")},
    )


@app.post("/clientes/{client_id}/editar")
def cliente_editar(
    client_id: int,
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        return RedirectResponse("/clientes", status_code=302)

    client.name = name.strip()
    client.phone = phone.strip()
    client.email = email.strip()
    client.notes = notes
    db.commit()
    return RedirectResponse(f"/clientes/{client_id}", status_code=303)


@app.post("/clientes/{client_id}/nota")
def cliente_nota(client_id: int, text_note: str = Form(...), db: Session = Depends(get_db)):
    if not text_note.strip():
        return RedirectResponse(f"/clientes/{client_id}?error=La+nota+está+vacía", status_code=303)
    db.add(ClientNote(client_id=client_id, text=text_note.strip()))
    db.commit()
    return RedirectResponse(f"/clientes/{client_id}", status_code=303)


@app.post("/clientes/nuevo")
def nuevo_cliente(
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    db.add(Client(name=name, phone=phone, email=email, notes=notes))
    db.commit()
    return RedirectResponse("/clientes", status_code=303)


# ---------- ADMIN ----------
@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, db: Session = Depends(get_db)):
    specialties = db.query(Specialty).order_by(Specialty.name.asc()).all()
    staff_list = db.query(Staff).order_by(Staff.name.asc()).all()
    salons = db.query(Salon).order_by(Salon.name.asc()).all()
    return templates.TemplateResponse("admin.html", {"request": request, "specialties": specialties, "staff": staff_list, "salons": salons})


@app.post("/admin/especialidades/nueva")
def nueva_especialidad(
    name: str = Form(...),
    color_hex: str = Form("#16a34a"),
    db: Session = Depends(get_db),
):
    db.add(Specialty(name=name.strip(), duration_min=0, color_hex=color_hex))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/staff/nuevo")
def nuevo_staff(name: str = Form(...), db: Session = Depends(get_db)):
    db.add(Staff(name=name.strip()))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/salones/nuevo")
def nuevo_salon(name: str = Form(...), db: Session = Depends(get_db)):
    db.add(Salon(name=name.strip()))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.get("/compras", response_class=HTMLResponse)
def compras(request: Request):
    return templates.TemplateResponse("compras.html", {"request": request})
