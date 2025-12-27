from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from datetime import date, datetime, timedelta, time

from db import Base, engine, SessionLocal
from models import Specialty, Staff, Client, Appointment

Base.metadata.create_all(bind=engine)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Static
app.mount("/static", StaticFiles(directory="static"), name="static")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse("/turnos", status_code=302)


def build_grid(selected_date: date, step_min: int = 30):
    """Grilla base 08:00 a 19:00 cada 30 min"""
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
def turnos(request: Request, date_str: str = None, staff_id: int = None, db: Session = Depends(get_db)):
    # Fecha
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except:
            selected_date = date.today()
    else:
        selected_date = date.today()

    # Staff (puede estar vacío en Render)
    staff_list = db.query(Staff).order_by(Staff.name.asc()).all()
    if staff_id is None and staff_list:
        staff_id = staff_list[0].id

    # Turnos del día (si hay staff, filtra; si no hay staff, muestra igual grilla)
    q = db.query(Appointment).filter(Appointment.date == selected_date)
    if staff_id:
        q = q.filter(Appointment.staff_id == staff_id)
    appointments = q.order_by(Appointment.start_time.asc()).all()

    # Grilla base
    grid = build_grid(selected_date, step_min=30)

    # Index por hora
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
                tt = (datetime.combine(selected_date, t) + timedelta(minutes=30 * i)).time()
                blocked.add(tt)

    # Etiqueta día
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
        "grid": grid,
    })


@app.get("/turnos/nuevo", response_class=HTMLResponse)
def turnos_nuevo(request: Request, date_str: str = None, time_str: str = None, db: Session = Depends(get_db)):
    clients = db.query(Client).order_by(Client.name.asc()).all()
    specialties = db.query(Specialty).order_by(Specialty.name.asc()).all()
    staff = db.query(Staff).order_by(Staff.name.asc()).all()

    return templates.TemplateResponse("turnos_nuevo.html", {
        "request": request,
        "clients": clients,
        "specialties": specialties,
        "staff": staff,
        "date_str": date_str or "",
        "time_str": time_str or "",
    })


@app.get("/clientes", response_class=HTMLResponse)
def clientes(request: Request, q: str = "", db: Session = Depends(get_db)):
    base_q = db.query(Client)
    if q:
        like = f"%{q.strip()}%"
        base_q = base_q.filter((Client.name.ilike(like)) | (Client.phone.ilike(like)))
    clients = base_q.order_by(Client.name.asc()).all()

    return templates.TemplateResponse("clientes.html", {"request": request, "clients": clients, "q": q})


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
    color_hex: str = Form("#6D28D9"),
    db: Session = Depends(get_db),
):
    db.add(Specialty(name=name, color_hex=color_hex))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/staff/nuevo")
def nuevo_staff(name: str = Form(...), db: Session = Depends(get_db)):
    db.add(Staff(name=name))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.get("/compras", response_class=HTMLResponse)
def compras(request: Request):
    return templates.TemplateResponse("compras.html", {"request": request})
