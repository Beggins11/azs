import logging
import time

from flask import Flask, jsonify, render_template, request

from repository import FuelRepository, InMemoryFuelRepository

CHANNELS = {"pump": "На колонке", "operator": "Через оператора"}


class TimingMiddleware:
    """WSGI-middleware: пишет в лог метод, путь, статус и время обработки."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app
        self.log = logging.getLogger("azs.requests")

    def __call__(self, environ, start_response):
        started = time.perf_counter()
        status_holder = {}

        def _start_response(status, headers, exc_info=None):
            status_holder["status"] = status
            return start_response(status, headers, exc_info)

        try:
            return self.wsgi_app(environ, _start_response)
        finally:
            ms = (time.perf_counter() - started) * 1000
            self.log.info("%s %s -> %s (%.1f мс)", environ["REQUEST_METHOD"],
                          environ["PATH_INFO"], status_holder.get("status", "?"), ms)


def create_app(repo: FuelRepository | None = None) -> Flask:
    """Фабрика приложения: репозиторий передаётся снаружи."""
    app = Flask(__name__)
    repo = repo or InMemoryFuelRepository()
    app.wsgi_app = TimingMiddleware(app.wsgi_app)

    def role():
        return request.headers.get("X-Role", "operator")

    def forbidden():
        return jsonify(error="Это действие доступно только менеджеру"), 403

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/tanks")
    def tanks():
        return jsonify(repo.list_tanks())

    @app.post("/api/tanks")
    def add_tank():
        if role() != "manager":
            return forbidden()
        d = request.get_json(silent=True) or {}
        try:
            mark = str(d["mark"]).strip()
            capacity, level, price = float(d["capacity"]), float(d["level"]), float(d["price"])
        except (KeyError, ValueError):
            return jsonify(error="Заполните все поля числами"), 400
        if not mark or capacity <= 0 or price <= 0 or not 0 <= level <= capacity:
            return jsonify(error="Проверьте значения: остаток не больше объёма, цена больше нуля"), 400
        return jsonify(repo.add_tank(mark, capacity, level, price)), 201

    @app.post("/api/sales")
    def add_sale():
        d = request.get_json(silent=True) or {}
        try:
            tank_id, liters, paid = int(d["tank_id"]), float(d["liters"]), float(d["paid"])
            channel = d["channel"]
        except (KeyError, ValueError):
            return jsonify(error="Заполните все поля"), 400
        if channel not in CHANNELS or liters <= 0 or paid < 0:
            return jsonify(error="Некорректные данные заправки"), 400
        try:
            return jsonify(repo.add_sale(tank_id, liters, channel, paid)), 201
        except KeyError as e:
            return jsonify(error=str(e.args[0])), 404
        except ValueError as e:
            return jsonify(error=str(e)), 409

    @app.get("/api/sales")
    def sales():
        return jsonify(repo.list_sales())

    @app.get("/api/report")
    def report():
        if role() != "manager":
            return forbidden()
        sales_ = repo.list_sales()
        by_mark = {}
        for s in sales_:
            m = by_mark.setdefault(s["mark"], {"mark": s["mark"], "liters": 0, "revenue": 0})
            m["liters"] = round(m["liters"] + s["liters"], 2)
            m["revenue"] = round(m["revenue"] + s["paid"], 2)
        rating = sorted(by_mark.values(), key=lambda x: x["liters"], reverse=True)
        channels = {k: {"name": v, "count": 0, "sum": 0} for k, v in CHANNELS.items()}
        for s in sales_:
            channels[s["channel"]]["count"] += 1
            channels[s["channel"]]["sum"] = round(channels[s["channel"]]["sum"] + s["paid"], 2)
        return jsonify(
            total_liters=round(sum(s["liters"] for s in sales_), 2),
            total_revenue=round(sum(s["paid"] for s in sales_), 2),
            rating=rating,
            channels=list(channels.values()),
            problems=[s for s in sales_ if not s["ok"]],
            tanks=repo.list_tanks(),
        )

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    application = create_app()
    from waitress import serve
    print("Сайт: http://localhost:8080")
    serve(application, host="127.0.0.1", port=8080)
