import logging
from flask import Flask, jsonify, request, Response
from . import database as db
from . import config

log = logging.getLogger("localowl.server")

app = Flask(__name__)


@app.after_request
def _cors(resp: Response) -> Response:
    resp.headers["Access-Control-Allow-Origin"]  = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, PUT, OPTIONS"
    return resp


@app.route("/api/status")
def status():
    return jsonify({
        "repos":          config.GITHUB_REPOS,
        "poll_interval":  config.POLL_INTERVAL,
        "skip_drafts":    config.SKIP_DRAFT_PRS,
        "recheck_on_push": config.RECHECK_UPDATED_PRS,
        "total_reviews":  db.count_reviews(),
    })


@app.route("/api/reviews")
def reviews():
    limit   = min(int(request.args.get("limit",  50)),  200)
    offset  = int(request.args.get("offset", 0))
    verdict = request.args.get("verdict") or None
    repo    = request.args.get("repo")    or None
    items   = db.get_reviews(limit, offset, verdict, repo)
    total   = db.count_reviews(verdict, repo)
    return jsonify({"items": items, "total": total, "offset": offset})


@app.route("/api/settings", methods=["GET", "OPTIONS"])
def get_settings():
    if request.method == "OPTIONS":
        return "", 204
    return jsonify(db.get_settings())


@app.route("/api/settings", methods=["PUT", "OPTIONS"])
def put_settings():
    if request.method == "OPTIONS":
        return "", 204
    data = request.get_json(silent=True) or {}
    db.save_settings(data)
    return jsonify({"ok": True})


def start(host: str = "0.0.0.0", port: int = config.SERVER_PORT) -> None:
    log.info("API server listening on http://%s:%d", host, port)
    import logging as _l
    _l.getLogger("werkzeug").setLevel(_l.WARNING)
    app.run(host=host, port=port, debug=False, use_reloader=False)
