from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, datetime, timedelta, time

from db import Base, engine, SessionLocal
from models import Specialty, Staff, Client, Appointment, Salon

Base.metadata.create_all(bind=engine)

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed_salons(db: Session):
    if db.query(Salon).count() == 0:
        db.add(Salon(name="Salon 1"))
        db.add(Salon(name="Salon 2"))
        db.commit()


@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse("/turnos", status_code=302)


def build_grid(selected_date: date, step_min: int = 30):
    start_t = time(8, 0)
    end_t = time(19, 0)
    cur = datetime.combine(selected_date, start_t)
    end_dt = datetime.combine(selected_date, end_t)

    grid = []
    while cur <= end_dt:
        grid.append({"time": cur.time(), "time_str": cur.strftime("%H:%M"), "state": "free"})
        cur += timedelta(minutes=step_min)
    return grid


@app.get("/turnos", response_class=HTMLResponse)
def turnos(
    request: Request,
    date_str: str = None,
    staff_id: int = None,
    salon_id: int = None,
    db: Session = Depends(get_db)
):
    seed_salons(db)

    # Fecha seleccionada (si no viene, mostrar el próximo día con turnos; si no hay, hoy)
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except:
            selected_date = date.today()
    else:
        next_day = db.query(func.min(Appointment.date)).filter(Appointment.status != "CANCELADO").scalar()
        selected_date = next_day or date.today()

    staff_list = db.query(Staff).order_by(Staff.name.asc()).all()
    salon_list = db.query(Salon).order_by(Salon.id.asc()).all()

    if staff_id is None and staff_list:
        staff_id = staff_list[0].id
    if salon_id is None and salon_list:
        salon_id = salon_list[0].id

    # Turnos del día filtrados por staff/salon si existen
    q = db.query(Appointment).filter(Appointment.date == selected_date, Appointment.status != "CANCELADO")
    if staff_id:
        q = q.filter(Appointment.staff_id == staff_id)
    if salon_id:
        q = q.filter(Appointment.salon_id == salon_id)

    appointments = q.order_by(Appointment.start_time.asc()).all()

    # Días con turnos (para marcar en calendario) - mes actual +/- 1 mes
    month_start = selected_date.replace(day=1)
    month_end = (month_start + timedelta(days=40)).replace(day=1) - timedelta(days=1)
    days_with = db.query(Appointment.date).filter(
        Appointment.date >= month_start,
        Appointment.date <= month_end,
        Appointment.status != "CANCELADO"
    ).distinct().all()
    days_with_turnos = {d[0].strftime("%Y-%m-%d") for d in days_with}

    # Grilla
    grid = build_grid(selected_date, 30)
    appt_by_time = {a.start_time: a for a in appointments}

    blocked = set()
    for row in grid:
        t = row["time"]
        if t in blocked:
            row["state"] = "blocked"
            continue

        a = appt_by_time.get(t)
        if not a:
            row["state"] = "free"
        else:
            row["state"] = "busy"
            row["appt"] = a
            dur = int(a.duration_min or 30)
            slots = max(1, dur // 30)
            for i in range(slots):
                tt = (datetime.combine(selected_date, t) + timedelta(minutes=30*i)).time()
                blocked.add(tt)

    dias = ["LUNES","MARTES","MIÉRCOLES","JUEVES","VIERNES","SÁBADO","DOMINGO"]
    day_name = dias[selected_date.weekday()]
    date_label = selected_date.strftime("%d/%m/%Y")

    return templates.TemplateResponse("turnos.html", {
        "request": request,
        "selected_date": selected_date.strftime("%Y-%m-%d"),
        "day_name": day_name,
        "date_label": date_label,
        "staff_list": staff_list,
        "staff_id": staff_id,
        "salon_list": salon_list,
        "salon_id": salon_id,
        "grid": grid,
        "days_with_turnos": sorted(list(days_with_turnos)),
    })


@app.get("/turnos/nuevo", response_class=HTMLResponse)
def turnos_nuevo(
    request: Request,
    date_str: str = "",
    time_str: str = "",
    db: Session = Depends(get_db)
):
    seed_salons(db)
    clients = db.query(Client).order_by(Client.name.asc()).all()
    specialties = db.query(Specialty).order_by(Specialty.name.asc()).all()
    staff = db.query(Staff).order_by(Staff.name.asc()).all()
    salons = db.query(Salon).order_by(Salon.id.asc()).all()

    return templates.TemplateResponse("turnos_nuevo.html", {
        "request": request,
        "clients": clients,
        "specialties": specialties,
        "staff": staff,
        "salons": salons,
        "date_str": date_str,
        "time_str": time_str,
    })


@app.post("/turnos/nuevo")
def crear_turno(
    date_str: str = Form(...),
    time_str: str = Form(...),
    duration_min: int = Form(30),
    client_id: int = Form(0),
    new_client_name: str = Form(""),
    new_client_phone: str = Form(""),
    specialty_id: int = Form(0),
    staff_id: int = Form(0),
    salon_id: int = Form(0),
    deposit_paid: str = Form("off"),
    deposit_amount: int = Form(0),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    seed_salons(db)

    appt_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    appt_time = datetime.strptime(time_str, "%H:%M").time()

    # Cliente: existente o nuevo
    if client_id == 0:
        if not new_client_name.strip():
            return RedirectResponse(f"/turnos/nuevo?date_str={date_str}&time_str={time_str}", status_code=303)
        c = Client(name=new_client_name.strip(), phone=new_client_phone.strip())
        db.add(c)
        db.commit()
        db.refresh(c)
        client_id = c.id

    dep_paid = (deposit_paid == "on")

    a = Appointment(
        date=appt_date,
        start_time=appt_time,
        duration_min=duration_min,
        client_id=client_id,
        specialty_id=specialty_id if specialty_id else None,
        staff_id=staff_id if staff_id else None,
        salon_id=salon_id if salon_id else None,
        deposit_paid=dep_paid,
        deposit_amount=deposit_amount if dep_paid else 0,
        notes=notes or "",
        status="ACTIVO"
    )

    db.add(a)

    # Actualizar última visita del cliente (cuando se crea turno)
    client = db.query(Client).get(client_id)
    if client:
        client.last_visit = appt_date

    db.commit()

    return RedirectResponse(f"/turnos?date_str={date_str}&staff_id={staff_id}&salon_id={salon_id}", status_code=303)


@app.get("/turnos/{appt_id}/editar", response_class=HTMLResponse)
def editar_turno(appt_id: int, request: Request, db: Session = Depends(get_db)):
    seed_salons(db)
    a = db.query(Appointment).get(appt_id)
    if not a:
        return RedirectResponse("/turnos", status_code=302)

    clients = db.query(Client).order_by(Client.name.asc()).all()
    specialties = db.query(Specialty).order_by(Specialty.name.asc()).all()
    staff = db.query(Staff).order_by(Staff.name.asc()).all()
    salons = db.query(Salon).order_by(Salon.id.asc()).all()

    return templates.TemplateResponse("turno_editar.html", {
        "request": request,
        "a": a,
        "clients": clients,
        "specialties": specialties,
        "staff": staff,
        "salons": salons,
    })


@app.post("/turnos/{appt_id}/editar")
def guardar_edicion_turno(
    appt_id: int,
    date_str: str = Form(...),
    time_str: str = Form(...),
    duration_min: int = Form(30),
    client_id: int = Form(...),
    specialty_id: int = Form(0),
    staff_id: int = Form(0),
    salon_id: int = Form(0),
    deposit_paid: str = Form("off"),
    deposit_amount: int = Form(0),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    a = db.query(Appointment).get(appt_id)
    if not a:
        return RedirectResponse("/turnos", status_code=302)

    a.date = datetime.strptime(date_str, "%Y-%m-%d").date()
    a.start_time = datetime.strptime(time_str, "%H:%M").time()
    a.duration_min = duration_min
    a.client_id = client_id
    a.specialty_id = specialty_id if specialty_id else None
    a.staff_id = staff_id if staff_id else None
    a.salon_id = salon_id if salon_id else None
    a.deposit_paid = (deposit_paid == "on")
    a.deposit_amount = deposit_amount if a.deposit_paid else 0
    a.notes = notes or ""

    # actualizar última visita del cliente
    client = db.query(Client).get(client_id)
    if client:
        client.last_visit = a.date

    db.commit()
    return RedirectResponse(f"/turnos?date_str={date_str}&staff_id={staff_id}&salon_id={salon_id}", status_code=303)


@app.post("/turnos/{appt_id}/cancelar")
def cancelar_turno(appt_id: int, db: Session = Depends(get_db)):
    a = db.query(Appointment).get(appt_id)
    if a:
        a.status = "CANCELADO"
        db.commit()
    return RedirectResponse("/turnos", status_code=303)


@app.get("/clientes", response_class=HTMLResponse)
def clientes(request: Request, q: str = "", db: Session = Depends(get_db)):
    base_q = db.query(Client)
    if q:
        like = f"%{q.strip()}%"
        base_q = base_q.filter((Client.name.ilike(like)) | (Client.phone.ilike(like)))
    clients = base_q.order_by(Client.name.asc()).all()
    return templates.TemplateResponse("clientes.html", {"request": request, "clients": clients, "q": q})


@app.get("/clientes/{client_id}", response_class=HTMLResponse)
def cliente_ficha(client_id: int, request: Request, db: Session = Depends(get_db)):
    c = db.query(Client).get(client_id)
    if not c:
        return RedirectResponse("/clientes", status_code=302)

    last = c.last_visit.strftime("%d/%m/%Y") if c.last_visit else "—"
    return templates.TemplateResponse("cliente_ficha.html", {"request": request, "c": c, "last": last})


@app.post("/clientes/{client_id}/editar")
def cliente_editar(
    client_id: int,
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    c = db.query(Client).get(client_id)
    if c:
        c.name = name
        c.phone = phone
        c.email = email
        c.notes = notes
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


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, db: Session = Depends(get_db)):
    specialties = db.query(Specialty).order_by(Specialty.name.asc()).all()
    staff = db.query(Staff).order_by(Staff.name.asc()).all()
    return templates.TemplateResponse("admin.html", {"request": request, "specialties": specialties, "staff": staff})


@app.post("/admin/especialidades/nueva")
def nueva_especialidad(
    name: str = Form(...),
    color_hex: str = Form("#F5C542"),
    db: Session = Depends(get_db),
):
    db.add(Specialty(name=name.strip(), color_hex=color_hex))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/especialidades/{sid}/eliminar")
def eliminar_especialidad(sid: int, db: Session = Depends(get_db)):
    s = db.query(Specialty).get(sid)
    if s:
        db.delete(s)
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/staff/nuevo")
def nuevo_staff(name: str = Form(...), db: Session = Depends(get_db)):
    db.add(Staff(name=name.strip()))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/staff/{sid}/eliminar")
def eliminar_staff(sid: int, db: Session = Depends(get_db)):
    s = db.query(Staff).get(sid)
    if s:
        db.delete(s)
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.get("/compras", response_class=HTMLResponse)
def compras(request: Request):
    return templates.TemplateResponse("compras.html", {"request": request})
