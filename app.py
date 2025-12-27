from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from datetime import date, datetime, time, timedelta

from db import Base, engine, SessionLocal
from models import Specialty, Staff, Client, Appointment

Base.metadata.create_all(bind=engine)

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

STUDIO_WA_NUMBER = "5491167253722"  # número del estudio

# ---------------- DB ----------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---------------- PHONE / WA ----------------
def _digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())

def normalize_ar_phone_to_wa(phone_raw: str) -> str:
    """
    Entrada típica: 11xxxxxxxx (sin +54 9).
    Salida WhatsApp: 54911xxxxxxxx
    """
    p = _digits(phone_raw)

    if p.startswith("549") and len(p) >= 12:
        return p

    if p.startswith("54") and len(p) >= 11:
        if p.startswith("549"):
            return p
        # 54 + 11xxxx -> 54911xxxx
        if len(p) >= 12 and p[2:4] == "11":
            return "549" + p[2:]
        return p

    if p.startswith("11") and len(p) >= 10:
        return "549" + p

    return p

def make_wa_message(appt: Appointment) -> str:
    dias = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]
    dname = dias[appt.date.weekday()]
    dnum = appt.date.day
    hora = appt.start_time.strftime("%H:%M")
    return (
        f"Cómo estás? Quería recordarte que el día {dname}, {dnum} y {hora}, "
        f"tenemos agendado un turno. Te esperamos!! Estudio Angie´s Color-"
    )

# ---------------- SLOTS ----------------
def build_slots(start_h=8, end_h=19, step_min=30):
    slots = []
    t = datetime.combine(date.today(), time(start_h, 0))
    end = datetime.combine(date.today(), time(end_h, 0))
    while t <= end:
        slots.append(t.time())
        t += timedelta(minutes=step_min)
    return slots

SLOTS = build_slots()

def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute

def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    # intervalos [start, end)
    return a_start < b_end and b_start < a_end

def assert_no_overlap(
    db: Session,
    d: date,
    start_t: time,
    dur_min: int,
    staff_id: int | None,
    salon: int,
    exclude_appt_id: int | None = None,
):
    """Evita solapamientos en mismo día + mismo staff + mismo salón."""
    a_start = _minutes(start_t)
    a_end = a_start + int(dur_min)

    q = db.query(Appointment).filter(
        Appointment.date == d,
        Appointment.status != "CANCELADO",
        Appointment.salon == salon,
    )
    if staff_id:
        q = q.filter(Appointment.staff_id == staff_id)

    appts = q.all()

    for ap in appts:
        if exclude_appt_id and ap.id == exclude_appt_id:
            continue
        b_start = _minutes(ap.start_time)
        b_end = b_start + int(ap.duration_min or 0)
        if _overlap(a_start, a_end, b_start, b_end):
            raise HTTPException(
                status_code=400,
                detail="Ese horario se superpone con otro turno (por duración)."
            )

def build_slot_state(slots: list[time], appts: list[Appointment]) -> dict:
    """
    Devuelve un dict por hora:
    - FREE
    - APPT (solo en hora de inicio)
    - BLOCKED (slots intermedios ocupados por duración)
    """
    state = {}
    for t in slots:
        state[t.strftime("%H:%M")] = {"kind": "FREE"}

    # Orden por inicio
    appts = sorted(appts, key=lambda a: _minutes(a.start_time))

    for a in appts:
        start_min = _minutes(a.start_time)
        end_min = start_min + int(a.duration_min or 0)

        # marca slot inicio
        k = a.start_time.strftime("%H:%M")
        state[k] = {"kind": "APPT", "appt": a}

        # marca slots siguientes como BLOQUEADOS
        for t in slots:
            m = _minutes(t)
            if m > start_min and m < end_min:
                kk = t.strftime("%H:%M")
                # no pisar si hay otro turno que empieza justo ahí (pero igual sería solapado)
                if state.get(kk, {}).get("kind") == "FREE":
                    state[kk] = {
                        "kind": "BLOCKED",
                        "owner_id": a.id,
                        "owner_start": a.start_time.strftime("%H:%M"),
                        "owner_end": (datetime.combine(date.today(), a.start_time) + timedelta(minutes=int(a.duration_min or 0))).time().strftime("%H:%M")
                    }

    return state

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

    # last_visit lo dejás como lo tenías (si lo manejás en templates ya ok)
    return templates.TemplateResponse("clientes.html", {
        "request": request,
        "clients": filtered,
        "q": q
    })

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
    # si usás last_visit en template, calculalo simple desde appointments:
    last_appt = db.query(Appointment).filter(
        Appointment.client_id == c.id,
        Appointment.status != "CANCELADO"
    ).order_by(Appointment.date.desc(), Appointment.start_time.desc()).first()
    last = last_appt.date.strftime("%d/%m/%Y") if last_appt else "—"
    return templates.TemplateResponse("cliente_ficha.html", {"request": request, "c": c, "last": last})

@app.post("/clientes/{client_id}/editar")
def cliente_guardar(
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
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "specialties": specialties,
        "staff": staff
    })

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
def borrar_especialidad(sid: int, db: Session = Depends(get_db)):
    sp = db.query(Specialty).filter(Specialty.id == sid).first()
    if not sp:
        raise HTTPException(status_code=404, detail="Not Found")
    db.delete(sp)
    db.commit()
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/staff/{sid}/eliminar")
def borrar_staff(sid: int, db: Session = Depends(get_db)):
    st = db.query(Staff).filter(Staff.id == sid).first()
    if not st:
        raise HTTPException(status_code=404, detail="Not Found")
    db.delete(st)
    db.commit()
    return RedirectResponse("/admin", status_code=303)

# ---------------- TURNOS ----------------
def nearest_open_date(db: Session) -> date:
    today = date.today()
    appts = db.query(Appointment).filter(
        Appointment.status != "CANCELADO"
    ).order_by(Appointment.date.asc()).all()
    for a in appts:
        if a.date >= today:
            return a.date
    return today

@app.get("/turnos", response_class=HTMLResponse)
def turnos(request: Request, date_str: str = "", staff_id: int = 0, salon: int = 1, db: Session = Depends(get_db)):
    # fecha
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

    q = db.query(Appointment).filter(
        Appointment.date == selected_date,
        Appointment.status != "CANCELADO",
        Appointment.salon == salon
    )
    if staff_id:
        q = q.filter(Appointment.staff_id == staff_id)

    day_appts = q.order_by(Appointment.start_time.asc()).all()

    slot_state = build_slot_state(SLOTS, day_appts)

    open_dates = db.query(Appointment.date).filter(
        Appointment.status != "CANCELADO"
    ).distinct().all()
    open_dates = sorted({d[0].strftime("%Y-%m-%d") for d in open_dates})

    weekday_labels = ["LUNES","MARTES","MIÉRCOLES","JUEVES","VIERNES","SÁBADO","DOMINGO"]

    return templates.TemplateResponse("turnos.html", {
        "request": request,
        "selected_date": selected_date.strftime("%Y-%m-%d"),
        "selected_date_label": selected_date.strftime("%d/%m/%Y"),
        "selected_weekday": weekday_labels[selected_date.weekday()],
        "slots": [t.strftime("%H:%M") for t in SLOTS],
        "slot_state": slot_state,
        "staffs": staffs,
        "staff_id": staff_id,
        "salon": salon,
        "open_dates": open_dates,
        "studio_wa": STUDIO_WA_NUMBER
    })

@app.get("/turnos/nuevo", response_class=HTMLResponse)
def turnos_nuevo(request: Request, date_str: str = "", time_str: str = "", staff_id: int = 0, salon: int = 1, db: Session = Depends(get_db)):
    clients = db.query(Client).order_by(Client.name.asc()).all()
    specialties = db.query(Specialty).order_by(Specialty.name.asc()).all()
    staff = db.query(Staff).order_by(Staff.name.asc()).all()

    return templates.TemplateResponse("turnos_nuevo.html", {
        "request": request,
        "clients": clients,
        "specialties": specialties,
        "staff": staff,
        "date_str": date_str,
        "time_str": time_str,
        "staff_id": staff_id,
        "salon": salon
    })

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
    # parse fecha/hora
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        t = datetime.strptime(time_str, "%H:%M").time()
    except:
        raise HTTPException(status_code=400, detail="Fecha u hora inválida")

    if salon not in (1, 2):
        salon = 1

    # cliente
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

    # VALIDAR solapamiento por duración
    assert_no_overlap(
        db=db,
        d=d,
        start_t=t,
        dur_min=int(duration_min),
        staff_id=(staff_id if staff_id > 0 else None),
        salon=salon,
        exclude_appt_id=None
    )

    appt = Appointment(
        date=d,
        start_time=t,
        duration_min=int(duration_min),
        client_id=client.id,
        specialty_id=(specialty_id if specialty_id > 0 else None),
        staff_id=(staff_id if staff_id > 0 else None),
        salon=salon,
        deposit_paid=(deposit_paid == "1"),
        deposit_amount=(int(deposit_amount) if deposit_paid == "1" else 0),
        notes=notes.strip(),
        status="ACTIVO",
        wa_sent=False
    )
    db.add(appt)
    db.commit()

    return RedirectResponse(f"/turnos?date_str={date_str}&staff_id={staff_id}&salon={salon}", status_code=303)

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
    phone = normalize_ar_phone_to_wa(appt.client.phone)
    msg = make_wa_message(appt)
    import urllib.parse
    url = f"https://wa.me/{phone}?text={urllib.parse.quote(msg)}"
    return JSONResponse({"url": url})
