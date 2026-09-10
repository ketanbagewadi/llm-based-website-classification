import os
from collections import defaultdict
from datetime import datetime

from flask import Flask, render_template, redirect, url_for, flash, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    login_required,
    logout_user,
    current_user,
)
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "secret_key")

default_db_path = os.path.join(app.root_path, "instance", "site.db")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", f"sqlite:///{default_db_path}")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# ======================== GIT INTEGRATION (OPTIONAL) ======================== #
# Git push is off by default. Turn it on later by setting GIT_ENABLED=true and
# pointing GIT_REPO_PATH at a local clone of your domains repo (with a
# `categories/` folder containing one file per category name).
GIT_ENABLED = os.getenv("GIT_ENABLED", "false").lower() == "true"
GIT_REPO_PATH = os.getenv("GIT_REPO_PATH", "/home/ketan/domaindb")
CATEGORIES_FOLDER = "categories"

Repo = None
if GIT_ENABLED:
    try:
        from git import Repo  # GitPython — only required if GIT_ENABLED=true
    except ImportError:
        app.logger.warning(
            "GIT_ENABLED=true but GitPython is not installed. "
            "Run `pip install GitPython` to enable Git push. Falling back to disabled."
        )
        GIT_ENABLED = False

# ======================== DATABASE MODELS ======================== #


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)


class Dom(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    domains = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(100))
    git_push = db.Column(db.Boolean, default=False)
    verified = db.Column(db.Boolean, default=False)
    unknown_domains = db.Column(db.Text)


class Unknown(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    unknown_domains = db.Column(db.String(255), nullable=False)
    added_at = db.Column(db.DateTime, default=db.func.current_timestamp())


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ======================== FORMS ======================== #


class RegistrationForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    confirm_password = PasswordField("Confirm Password", validators=[DataRequired(), EqualTo("password")])
    submit = SubmitField("Register")


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Login")


CATEGORIES = [
    "Adult Content",
    "Advertisement",
    "Alcohol and Drugs",
    "Banking and Finance",
    "Blogs",
    "Business",
    "Chemistry",
    "Crypto",
    "Dating",
    "DoH Providers",
    "Education",
    "Entertainment",
    "Fortunetelling",
    "Gamble",
    "Games",
    "Global Religion",
    "Government",
    "Homestyle",
    "Information Technology",
    "Job Search",
    "Media Converter and Editor",
    "Military",
    "Music",
    "News",
    "Peer to Peer",
    "Pets",
    "Porn",
    "Restaurant",
    "Search Engines and Portals",
    "Shopping",
    "Social Network",
    "Sports",
    "Telecommunication",
    "Trading",
    "Transport",
    "Travel",
    "Web mail",
    "Wellness",
]


@app.context_processor
def inject_categories():
    return {"CATEGORIES": CATEGORIES}


# ROUTES ===============================================================================================================================


@app.route("/")
def index():
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    form = RegistrationForm()
    if form.validate_on_submit():
        if User.query.filter_by(email=form.email.data).first():
            flash("Email already registered.", "danger")
            return redirect(url_for("register"))

        new_user = User(
            name=form.name.data,
            email=form.email.data,
            password=generate_password_hash(form.password.data),
        )

        db.session.add(new_user)
        db.session.commit()
        flash("Account created! You can now log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html", form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and check_password_hash(user.password, form.password.data):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("Invalid email or password", "danger")
    return render_template("login.html", form=form)


@app.route("/dashboard")
@login_required
def dashboard():
    pending_doms = Dom.query.filter_by(git_push=False).all()
    total_domains = Dom.query.count()
    verified_count = Dom.query.filter_by(verified=True).count()
    pushed_count = Dom.query.filter_by(git_push=True).count()

    return render_template(
        "dashboard.html",
        name=current_user.name,
        pending_doms=pending_doms,
        categories=CATEGORIES,
        total_domains=total_domains,
        verified_count=verified_count,
        pushed_count=pushed_count,
        git_enabled=GIT_ENABLED,
    )


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/verify/<int:dom_id>", methods=["POST"])
@login_required
def verify(dom_id):
    dom = Dom.query.get_or_404(dom_id)
    action = request.form.get("action")

    if action == "correct":
        dom.verified = True
        db.session.commit()
        return jsonify({"status": "success", "message": f"Domain {dom.domains} marked as correct."})

    elif action == "change":
        new_category = request.form.get("category")
        if new_category in CATEGORIES:
            dom.category = new_category
            dom.verified = True
            db.session.commit()
            return jsonify({"status": "success", "message": f"Domain {dom.domains} category changed to {new_category}."})
        else:
            return jsonify({"status": "error", "message": "Invalid category."}), 400

    elif action == "discard":
        domain_name = dom.domains
        db.session.delete(dom)
        db.session.commit()
        return jsonify({"status": "success", "message": f"Domain {domain_name} deleted from database."})

    elif action == "unverify":
        dom.verified = False
        db.session.commit()
        return jsonify({"status": "success", "message": f"Domain {dom.domains} unmarked (set back to pending)."})

    else:
        return jsonify({"status": "error", "message": "Invalid action."}), 400


@app.route("/update_domain/<int:dom_id>", methods=["POST"])
@login_required
def update_domain(dom_id):
    dom = Dom.query.get_or_404(dom_id)
    new_domain = request.form.get("new_domain")

    if not new_domain:
        return jsonify({"status": "error", "message": "New domain is required."}), 400

    dom.domains = new_domain
    db.session.commit()
    return jsonify({"status": "success", "message": f"Domain updated to {new_domain}."})


# ADD DOMAIN ================================================

@app.route("/add_domain", methods=["POST"])
@login_required
def add_domain():
    data = request.get_json()
    domain = data.get("domain", "").strip()
    category = data.get("category", "").strip()

    if not domain:
        return jsonify({"status": "error", "message": "Domain is required."}), 400

    if not category or category not in CATEGORIES:
        return jsonify({"status": "error", "message": "A valid category is required."}), 400

    # Check for duplicate
    existing = Dom.query.filter_by(domains=domain).first()
    if existing:
        return jsonify({"status": "error", "message": f"Domain '{domain}' already exists in the database."}), 400

    new_dom = Dom(
        domains=domain,
        category=category,
        verified=True,
        git_push=False,
    )
    db.session.add(new_dom)
    db.session.commit()

    return jsonify({
        "status": "success",
        "message": f"Domain '{domain}' added with category '{category}'.",
        "domain": {
            "id": new_dom.id,
            "domain": new_dom.domains,
            "category": new_dom.category,
        }
    })


# GIT PUSH (OPTIONAL) ====================================================================================================


@app.route("/push_to_git", methods=["POST"])
@login_required
def push_to_git():
    if not GIT_ENABLED:
        return jsonify({
            "status": "error",
            "message": (
                "Git push is not enabled. Set GIT_ENABLED=true and GIT_REPO_PATH "
                "to a local clone of your domains repo, then restart the app."
            ),
        }), 400

    try:
        if not os.path.exists(GIT_REPO_PATH):
            return jsonify({
                "status": "error",
                "message": f"Git repository path does not exist: {GIT_REPO_PATH}"
            }), 400

        categories_path = os.path.join(GIT_REPO_PATH, CATEGORIES_FOLDER)
        if not os.path.exists(categories_path):
            return jsonify({
                "status": "error",
                "message": f"Categories folder does not exist: {categories_path}"
            }), 400

        verified_domains = Dom.query.filter_by(verified=True, git_push=False).all()

        if not verified_domains:
            return jsonify({
                "status": "error",
                "message": "No verified domains to push."
            }), 400

        domains_by_category = defaultdict(list)
        for dom in verified_domains:
            if dom.category:
                domains_by_category[dom.category].append(dom.domains)

        if not domains_by_category:
            return jsonify({
                "status": "error",
                "message": "No domains with valid categories found."
            }), 400

        repo = Repo(GIT_REPO_PATH)
        origin = repo.remotes.origin

        origin.pull()

        files_updated = []
        domains_added = 0

        for category, domains in domains_by_category.items():
            file_path = os.path.join(categories_path, category)

            if not os.path.exists(file_path):
                return jsonify({
                    "status": "error",
                    "message": f"Category file '{category}' not found in {categories_path}"
                }), 400

            with open(file_path, 'r', encoding='utf-8') as f:
                existing_content = f.read()
                existing_domains = set(line.strip() for line in existing_content.splitlines() if line.strip())

            new_domains = [d for d in domains if d not in existing_domains]

            if not new_domains:
                continue

            new_domains_text = '\n'.join(new_domains)

            if existing_content and not existing_content.endswith('\n'):
                new_domains_text = '\n' + new_domains_text

            with open(file_path, 'a', encoding='utf-8') as f:
                f.write(new_domains_text + '\n')

            relative_path = os.path.join(CATEGORIES_FOLDER, category)
            files_updated.append(relative_path)
            domains_added += len(new_domains)

        if not files_updated:
            return jsonify({
                "status": "error",
                "message": "No new domains to add (all domains already exist in files)."
            }), 400

        repo.index.add(files_updated)

        commit_message = f"Add {domains_added} domains across {len(files_updated)} categories"
        commit = repo.index.commit(commit_message)

        origin.push()

        for dom in verified_domains:
            if dom.category in domains_by_category:
                dom.git_push = True
        db.session.commit()

        return jsonify({
            "status": "success",
            "message": "Successfully pushed to Git repository.",
            "details": {
                "files_updated": files_updated,
                "domains_added": domains_added,
                "commit_sha": commit.hexsha[:7],
                "commit_message": commit_message
            }
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({
            "status": "error",
            "message": f"Git push failed: {str(e)}",
            "error_type": type(e).__name__
        }), 500


# main ===================================================================================================================


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5003)
    # app.run(debug=True)
