from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from datetime import datetime, date, time
from urllib.parse import quote

from db import Base, engine, SessionLocal
from models import Specialty, Staff, Client, Appointment, ClientNote

Base.metadata.create_all(bind=engine)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


STUDIO_WA_NUMBER = "5491167253722"  # ✅ el del estudio (no se usa para enviar, solo referencia si querés)
# El recordatorio se envía AL CLIENTE, no al estudio.


def normalize_ar_whatsapp(phone_raw: str) -> str:
    """
    Convierte teléfonos 'como los pasan' (11..., 011..., +54..., 549..., etc.) a formato WhatsApp: 549XXXXXXXXXX
    Heurísticas comunes en Argentina (especialmente AMBA).
    """
    if not phone_raw:
        return ""

    digits = "".join(ch for ch in phone_raw if ch.isdigit())

    # Quitar prefijo 00 internacional
    if digits.startswith("00"):
        digits = digits[2:]

    # Quitar 0 inicial (011..., 0xxx...)
    if digits.startswith("0"):
        digits = digits[1:]

    # Quitar "15" típico: 11 15 xxxx xxxx
    if digits.startswith("11") and len(digits) >= 4 and digits[2:4] == "15":
        digits = "11" + digits[4:]

    # Si ya viene 549...
    if digits.startswith("549") and len(digits) >= 12:
        return digits

    # Si viene 54... sin 9
    if digits.startswith("54"):
        rest = digits[2:]
        if rest.startswith("9"):
            return digits
        return "549" + rest

    # Si viene 9... (raro pero pasa)
    if digits.startswith("9"):
        return "54" + digits

    # Caso común: 11xxxxxxxx (10 dígitos)
    if digits.startswith("11") and len(digits) == 10:
        return "549" + digits

    # Otros casos: si ya tiene 10-13 dígitos y no pudimos, intentamos anteponer 549
    if len(digits) >= 10:
        # último recurso: si no tiene país, ponemos 54 y 9
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


def validate_no_overlap(db: Session, appt_date: date, start_t: time, dur: int, exclude_id: int | None = None) -> bool:
    start_m = to_minutes(start_t)
    existing = db.query(Appointment).filter(Appointment.date == appt_date).all()
    for a in existing:
        if exclude_id and a.id == exclude_id:
            continue
        if a.status == "CANCELADO":
            continue
        a_start = to_minutes(a.start_time)
        if overlaps(start_m, dur, a_start, a.duration_min):
            return False
    return True


def build_day_grid(db: Session, appt_date: date):
    day_start = 8 * 60
    day_end = 19 * 60
    step = 30

    appts = (
        db.query(Appointment)
        .filter(Appointment.date == appt_date, Appointment.status != "CANCELADO")
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
                "client": a.client.name,
                "client_phone": a.client.phone or "",
                "specialty": a.specialty.name,
                "color": a.specialty.color_hex,
                "id": a.id,
                "deposit_paid": a.deposit_paid,
                "deposit_amount": a.deposit_amount,
                "duration_min": a.duration_min,
                "reminder_sent": bool(a.reminder_sent),
            })
        elif m in blocked:
            grid.append({"time": minutes_to_str(m), "state": "blocked"})
        else:
            grid.append({"time": minutes_to_str(m), "state": "free"})
    return grid


@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse("/turnos", status_code=302)


# ---------------- TURNOS ----------------
@app.get("/turnos", response_class=HTMLResponse)
def turnos(request: Request, date_str: str = "", db: Session = Depends(get_db)):
    selected = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()

    dates = (
        db.query(Appointment.date)
        .filter(Appointment.status != "CANCELADO")
        .distinct()
        .order_by(Appointment.date.asc())
        .all()
    )
    days_with_turns = [d[0].strftime("%Y-%m-%d") for d in dates]

    grid = build_day_grid(db, selected)

    return templates.TemplateResponse(
        "turnos.html",
        {
            "request": request,
            "selected_date": selected.strftime("%Y-%m-%d"),
            "days_with_turns": days_with_turns,
            "grid": grid,
        },
    )


@app.get("/turnos/{appt_id}/whatsapp")
def whatsapp_recordatorio(appt_id: int, db: Session = Depends(get_db)):
    appt = db.query(Appointment).filter(Appointment.id == appt_id).first()
    if not appt:
        return RedirectResponse("/turnos", status_code=302)

    client_phone = appt.client.phone or ""
    wa_to = normalize_ar_whatsapp(client_phone)

    # Si no se puede armar teléfono, volvemos al día (y listo)
    day_str = appt.date.strftime("%Y-%m-%d")
    if not wa_to:
        return RedirectResponse(f"/turnos?date_str={day_str}", status_code=303)

    dia = day_name_es(appt.date)
    nro_dia = appt.date.day
    hora = appt.start_time.strftime("%H:%M")

    msg = f"Cómo estás? Quería recordarte que el día {dia}, {nro_dia} a las {hora}, tenemos agendado un turno. Te esperamos!! Estudio Angie´s Color. Los Olmos N2291 y Ruta 26"
    url = f"https://wa.me/{wa_to}?text={quote(msg)}"

    # ✅ marcamos como enviado
    appt.reminder_sent = True
    appt.reminder_sent_at = datetime.utcnow()
    db.commit()

    return RedirectResponse(url, status_code=302)


@app.get("/turnos/nuevo", response_class=HTMLResponse)
def turnos_nuevo(request: Request, date_str: str = "", time_str: str = "", db: Session = Depends(get_db)):
    clients = db.query(Client).order_by(Client.name.asc()).all()
    specialties = db.query(Specialty).order_by(Specialty.name.asc()).all()
    staff = db.query(Staff).order_by(Staff.name.asc()).all()

    dates = (
        db.query(Appointment.date)
        .filter(Appointment.status != "CANCELADO")
        .distinct()
        .order_by(Appointment.date.asc())
        .all()
    )
    days_with_turns = [d[0].strftime("%Y-%m-%d") for d in dates]

    return templates.TemplateResponse(
        "turnos_nuevo.html",
        {
            "request": request,
            "clients": clients,
            "specialties": specialties,
            "staff": staff,
            "days_with_turns": days_with_turns,
            "pref_date": date_str,
            "pref_time": time_str,
            "error": request.query_params.get("error", ""),
        },
    )


@app.post("/turnos/nuevo")
def turnos_nuevo_post(
    fecha: str = Form(...),
    hora: str = Form(...),
    duration_min: int = Form(...),
    specialty_id: int = Form(...),

    client_mode: str = Form("existing"),
    client_id: int = Form(0),
    new_client_name: str = Form(""),
    new_client_phone: str = Form(""),

    staff_id: int = Form(0),

    deposit_paid: str = Form("off"),
    deposit_amount: int = Form(0),

    db: Session = Depends(get_db),
):
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

    if not validate_no_overlap(db, appt_date, appt_time, int(duration_min)):
        return RedirectResponse(f"/turnos/nuevo?date_str={fecha}&time_str={hora}&error=Horario+ocupado", status_code=303)

    final_staff_id = None if int(staff_id) == 0 else int(staff_id)
    paid = (deposit_paid == "on")
    amount = int(deposit_amount) if paid else 0

    appt = Appointment(
        date=appt_date,
        start_time=appt_time,
        duration_min=int(duration_min),
        client_id=final_client_id,
        specialty_id=int(specialty_id),
        staff_id=final_staff_id,
        notes="",
        status="CONFIRMADO",
        deposit_paid=paid,
        deposit_amount=amount,
        reminder_sent=False,
        reminder_sent_at=None,
    )
    db.add(appt)
    db.commit()

    return RedirectResponse(f"/turnos?date_str={fecha}", status_code=303)


# ---------------- CLIENTES ----------------
@app.get("/clientes", response_class=HTMLResponse)
def clientes(request: Request, db: Session = Depends(get_db)):
    clients = db.query(Client).order_by(Client.name.asc()).all()
    return templates.TemplateResponse("clientes.html", {"request": request, "clients": clients})


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
def cliente_nota(
    client_id: int,
    text: str = Form(...),
    db: Session = Depends(get_db),
):
    if not text.strip():
        return RedirectResponse(f"/clientes/{client_id}?error=La+nota+está+vacía", status_code=303)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        return RedirectResponse("/clientes", status_code=302)

    db.add(ClientNote(client_id=client_id, text=text.strip()))
    db.commit()
    return RedirectResponse(f"/clientes/{client_id}", status_code=303)


@app.post("/clientes/{client_id}/eliminar")
def cliente_eliminar(client_id: int, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if client:
        db.delete(client)
        db.commit()
    return RedirectResponse("/clientes", status_code=303)


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


# ---------------- ADMIN / COMPRAS (igual que ya tenías) ----------------
@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, db: Session = Depends(get_db)):
    specialties = db.query(Specialty).order_by(Specialty.name.asc()).all()
    staff = db.query(Staff).order_by(Staff.name.asc()).all()
    return templates.TemplateResponse("admin.html", {"request": request, "specialties": specialties, "staff": staff})

@app.post("/admin/especialidades/nueva")
def nueva_especialidad(
    name: str = Form(...),
    color_hex: str = Form("#16a34a"),
    db: Session = Depends(get_db),
):
    db.add(Specialty(name=name, duration_min=0, color_hex=color_hex))
    db.commit()
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/staff/nuevo")
def nuevo_staff(name: str = Form(...), db: Session = Depends(get_db)):
    db.add(Staff(name=name.strip()))
    db.commit()
    return RedirectResponse("/admin", status_code=303)

@app.get("/compras", response_class=HTMLResponse)
def compras(request: Request):
    return templates.TemplateResponse("compras.html", {"request": request})
