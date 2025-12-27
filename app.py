from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.properties import RelationshipProperty
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

# ---------------- Helpers ----------------
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
    t0 = datetime.combine(date.today(), time(start_h, 0))
    t1 = datetime.combine(date.today(), time(end_h, 0))
    t = t0
    while t <= t1:
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

def _salon_filter(AppointmentModel, salon_value: int):
    """
    Soporta 2 casos:
    - Appointment.salon es Integer (columna)
    - Appointment.salon es relationship (entonces usamos salon_id si existe)
    """
    if salon_value not in (1, 2):
        salon_value = 1

    # Si existe salon_id, lo usamos siempre (es lo correcto si hay relationship)
    if hasattr(AppointmentModel, "salon_id"):
        return getattr(AppointmentModel, "salon_id") == salon_value

    # Si no existe salon_id, intentamos ver si salon es relación o columna
    if hasattr(AppointmentModel, "salon"):
        prop = getattr(AppointmentModel, "salon").property
        if isinstance(prop, RelationshipProperty):
            # Es relación pero no hay salon_id -> no podemos filtrar por int
            # Devolvemos None para "no filtrar" en vez de romper
            return None
        else:
            # Es columna
            return getattr(AppointmentModel, "salon") == salon_value

    return None

def _assign_salon_kwargs(AppointmentModel, salon_value: int) -> dict:
    """
    Al crear Appointment:
    - si existe salon_id -> usar salon_id
    - si existe salon como columna -> usar salon
    """
    if salon_value not in (1, 2):
        salon_value = 1

    if hasattr(AppointmentModel, "salon_id"):
        return {"salon_id": salon_value}

    if hasattr(AppointmentModel, "salon"):
        prop = getattr(AppointmentModel, "salon").property
        if isinstance(prop, RelationshipProperty):
            # relación sin salon_id: no asignamos para no romper
            return {}
        return {"salon": salon_value}

    return {}

# ---------------- HOME ----------------
@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse("/turnos", status_code=302)

# ===========================
#          CLIENTES
# ===========================
@app.get("/clientes", response_class=HTMLResponse)
def clientes(request: Request, q: str = "", db: Session = Depends(get_db)):
    qn = (q or "").strip().lower()
    all_clients = db.query(Client).order_by(Client.name.asc()).all()

    if qn:
        filtered = [
            c for c in all_clients
            if qn in (c.name or "").lower() or qn in (c.phone or "")
        ]
    else:
        filtered = all_clients

    return templates.TemplateResponse(
        "clientes.html",
        {"request": request, "clients": filtered, "q": q},
    )

@app.post("/clientes/nuevo")
def nuevo_cliente(
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    c = Client(
        name=(name or "").strip(),
        phone=(phone or "").strip(),
        email=(email or "").strip(),
        notes=(notes or "").strip(),
    )
    db.add(c)
    db.commit()
    return RedirectResponse("/clientes", status_code=303)

@app.get("/clientes/{client_id}", response_class=HTMLResponse)
def cliente_ficha(request: Request, client_id: int, db: Session = Depends(get_db)):
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        return RedirectResponse("/clientes", status_code=302)

    history = (
        db.query(Appointment)
        .options(joinedload(Appointment.specialty), joinedload(Appointment.staff))
        .filter(Appointment.client_id == client_id, Appointment.status != "CANCELADO")
        .order_by(Appointment.date.desc(), Appointment.start_time.desc())
        .all()
    )

    return templates.TemplateResponse(
        "cliente_ficha.html",
        {"request": request, "c": c, "history": history},
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
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        return RedirectResponse("/clientes", status_code=303)

    c.name = (name or "").strip()
    c.phone = (phone or "").strip()
    c.email = (email or "").strip()
    c.notes = (notes or "").strip()
    db.commit()

    return RedirectResponse(f"/clientes/{client_id}", status_code=303)

@app.post("/clientes/{client_id}/borrar")
def cliente_borrar(client_id: int, db: Session = Depends(get_db)):
    c = db.query(Client).filter(Client.id == client_id).first()
    if c:
        db.delete(c)
        db.commit()
    return RedirectResponse("/clientes", status_code=303)

# ===========================
#           ADMIN
# ===========================
@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, db: Session = Depends(get_db)):
    specialties = db.query(Specialty).order_by(Specialty.name.asc()).all()
    staff = db.query(Staff).order_by(Staff.name.asc()).all()
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "specialties": specialties, "staff": staff},
    )

@app.post("/admin/especialidades/nueva")
def nueva_especialidad(
    name: str = Form(...),
    color_hex: str = Form("#60a5fa"),
    db: Session = Depends(get_db),
):
    db.add(Specialty(name=(name or "").strip().upper(), color_hex=(color_hex or "").strip()))
    db.commit()
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/staff/nuevo")
def nuevo_staff(name: str = Form(...), db: Session = Depends(get_db)):
    db.add(Staff(name=(name or "").strip().upper()))
    db.commit()
    return RedirectResponse("/admin", status_code=303)

# Tus templates usan /eliminar
@app.post("/admin/especialidades/{sid}/eliminar")
def eliminar_especialidad(sid: int, db: Session = Depends(get_db)):
    sp = db.query(Specialty).filter(Specialty.id == sid).first()
    if not sp:
        raise HTTPException(status_code=404, detail="Not Found")
    db.delete(sp)
    db.commit()
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/staff/{sid}/eliminar")
def eliminar_staff(sid: int, db: Session = Depends(get_db)):
    st = db.query(Staff).filter(Staff.id == sid).first()
    if not st:
        raise HTTPException(status_code=404, detail="Not Found")
    db.delete(st)
    db.commit()
    return RedirectResponse("/admin", status_code=303)

# ===========================
#           TURNOS
# ===========================
@app.get("/turnos", response_class=HTMLResponse)
def turnos(
    request: Request,
    date_str: str = "",
    staff_id: int = 0,
    salon: int = 0,
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
        .options(joinedload(Appointment.client), joinedload(Appointment.specialty), joinedload(Appointment.staff))
        .filter(
            Appointment.date == selected_date,
            Appointment.status != "CANCELADO",
        )
    )

    # filtro salon compatible
    salon_clause = _salon_filter(Appointment, salon)
    if salon_clause is not None:
        q = q.filter(salon_clause)

    if staff_id:
        q = q.filter(Appointment.staff_id == staff_id)

    day_appts = q.order_by(Appointment.start_time.asc()).all()
    by_time = {a.start_time.strftime("%H:%M"): a for a in day_appts}

    open_dates = (
        db.query(Appointment.date)
        .filter(Appointment.status != "CANCELADO")
        .distinct()
        .all()
    )
    open_dates = sorted({d[0].strftime("%Y-%m-%d") for d in open_dates})

    return templates.TemplateResponse(
        "turnos.html",
        {
            "request": request,
            "selected_date": selected_date.strftime("%Y-%m-%d"),
            "selected_date_label": selected_date.strftime("%d/%m/%Y"),
            "selected_weekday": ["LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO"][selected_date.weekday()],
            "slots": [t.strftime("%H:%M") for t in SLOTS],
            "by_time": by_time,
            "staffs": staffs,
            "staff_id": staff_id,
            "salon": salon,
            "open_dates": open_dates,
            "studio_wa": STUDIO_WA_NUMBER,
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
    staff = db.query(Staff).order_by(Staff.name.asc()).all()

    return templates.TemplateResponse(
        "turnos_nuevo.html",
        {
            "request": request,
            "clients": clients,
            "specialties": specialties,
            "staff": staff,
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
        if not (new_name or "").strip():
            raise HTTPException(status_code=400, detail="Falta nombre del cliente")
        client = Client(name=new_name.strip(), phone=(new_phone or "").strip())
        db.add(client)
        db.commit()
        db.refresh(client)

    salon_kwargs = _assign_salon_kwargs(Appointment, salon)

    appt = Appointment(
        date=d,
        start_time=t,
        duration_min=int(duration_min),
        client_id=client.id,
        specialty_id=(specialty_id if specialty_id > 0 else None),
        staff_id=(staff_id if staff_id > 0 else None),
        deposit_paid=(deposit_paid == "1"),
        deposit_amount=(int(deposit_amount) if deposit_paid == "1" else 0),
        notes=(notes or "").strip(),
        status="ACTIVO",
        wa_sent=False,
        **salon_kwargs,
    )
    db.add(appt)
    db.commit()

    return RedirectResponse(
        f"/turnos?date_str={date_str}&staff_id={staff_id}&salon={salon}",
        status_code=303,
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
    appt = (
        db.query(Appointment)
        .options(joinedload(Appointment.client))
        .filter(Appointment.id == appt_id)
        .first()
    )
    if not appt:
        raise HTTPException(status_code=404, detail="Not Found")

    phone = normalize_ar_phone_to_wa(appt.client.phone if appt.client else "")
    msg = make_wa_message(appt)

    import urllib.parse
    url = f"https://wa.me/{phone}?text={urllib.parse.quote(msg)}"
    return JSONResponse({"url": url})