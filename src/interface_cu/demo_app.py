"""Fictional, server-rendered legacy banking target. No automation API."""

from collections import defaultdict

from flask import Flask, Response, redirect, render_template, request


MEMBERS = {
    "12345": {"name": "Alex Example", "savings": "1,234.56"},
    "06789": {"name": "Morgan Sample", "savings": "9,876.54"},
}

SCENARIOS = (
    "normal", "validation", "member_not_found", "account_not_found", "permission_denied",
    "known_interstitial", "unknown_interstitial", "native_dialog", "session_expired",
    "slow_loading", "loading_exhausted", "transient_failure", "retry_exhausted",
    "persistent_error", "unsafe_redirect", "operator_challenge", "operator_challenge_after_retry",
)


def create_app(scenario: str = "normal") -> Flask:
    """Server-side failure injection only; automation sees ordinary rendered UI.

    Each separately started demo instance owns its counters. No reset/state API,
    cookies, or scenario query parameter is exposed to the automation process.
    """
    if scenario not in SCENARIOS:
        raise ValueError("UNKNOWN_DEMO_SCENARIO")
    app = Flask(__name__)
    visits = defaultdict(int)

    def state(heading, *, link=None, script=None, status=200):
        return render_template("bank.html", view="runtime", heading=heading,
                               retry_url=request.path, link=link, script=script), status

    @app.get("/")
    def shell():
        return render_template("shell.html")

    @app.get("/home")
    def home():
        return render_template("bank.html", view="home")

    @app.get("/members")
    def members():
        query = request.args.get("member_id")
        message = None
        if query is not None and scenario == "validation":
            message = "Member lookup rejected by validation rules."
        elif query is not None and (len(query) != 5 or not query.isascii() or not query.isdigit()):
            message = "Enter a five-digit fictional member ID."
        elif query is not None and (query not in MEMBERS or scenario == "member_not_found"):
            message = "No such member."
        record = MEMBERS.get(query) if query and not message else None
        return render_template("bank.html", view="search", query=query or "", record=record, message=message)

    @app.get("/members/<member_id>")
    def detail(member_id):
        if member_id not in MEMBERS:
            return state("Member not found", status=404)
        if scenario == "permission_denied":
            return state("Access denied for member records", status=403)
        if scenario == "session_expired":
            return state("Session expired", status=401)
        if scenario == "operator_challenge_after_retry":
            visits[request.path] += 1
            if visits[request.path] == 1:
                return state("Temporary service failure", link="Retry read", status=503)
        operator_scenario = scenario in {"operator_challenge", "operator_challenge_after_retry"}
        if operator_scenario and request.args.get("operator_ack") != "confirmed":
            return render_template("bank.html", view="challenge", member_id=member_id)
        return render_template("bank.html", view="detail", member_id=member_id, record=MEMBERS[member_id],
                               script="cleanup_ack" if operator_scenario else None)

    @app.get("/members/<member_id>/savings")
    def savings(member_id):
        if member_id not in MEMBERS:
            return state("Member not found", status=404)
        visits[request.path] += 1
        attempt = visits[request.path]
        if scenario == "account_not_found":
            return state("Savings account not found", status=404)
        if scenario == "known_interstitial" and attempt == 1:
            return state("Read-only notice", link="Continue to savings")
        if scenario == "unknown_interstitial":
            return state("Confirm operation")
        if scenario == "native_dialog":
            return state("Confirmation required", script="confirm")
        if scenario == "slow_loading" and attempt == 1:
            return state("Loading savings", script="reload")
        if scenario == "loading_exhausted":
            return state("Loading savings")
        if (scenario == "transient_failure" and attempt == 1) or scenario == "retry_exhausted":
            return state("Temporary service failure", link="Retry read", status=503)
        if scenario == "persistent_error":
            return state("Application error", status=500)
        if scenario == "unsafe_redirect":
            return redirect("/transfer")
        return render_template("bank.html", view="savings", member_id=member_id, record=MEMBERS[member_id])

    @app.get("/runtime.js")
    def runtime_script():
        # External same-origin script fits the existing CSP; no unsafe-inline JS.
        return Response("""
            if (document.body.dataset.runtime === 'reload') {
                setTimeout(() => location.reload(), 450);
            } else if (document.body.dataset.runtime === 'confirm') {
                const accepted = confirm('Confirm this unexpected banking operation?');
                document.querySelector('h1').textContent = accepted
                    ? 'Unexpected confirmation accepted' : 'Confirmation cancelled';
            } else if (document.body.dataset.runtime === 'cleanup_ack') {
                history.replaceState(null, '', location.pathname);
            }
        """, mimetype="application/javascript")

    @app.route("/transfer", methods=["GET", "POST"])
    def transfer():
        # Even a direct request can never mutate financial state.
        return "Simulated financial mutation is disabled.", 403

    @app.after_request
    def local_only(response):
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store"
        return response

    return app
