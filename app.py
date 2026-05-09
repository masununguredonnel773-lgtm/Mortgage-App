
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, PasswordField, SubmitField, SelectField
from wtforms.validators import DataRequired, Email, EqualTo, Length
from authlib.integrations.flask_client import OAuth

# Import core engine logic
from engine import calculate_self_employed_income, ai_verify, check_eligibility, get_officer_tips

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-12345')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///mortgage_records.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

oauth = OAuth(app)

# User Model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=True)
    full_name = db.Column(db.String(100))
    google_id = db.Column(db.String(100), unique=True, nullable=True)
    apple_id = db.Column(db.String(100), unique=True, nullable=True)
    role = db.Column(db.String(20), default='loan_officer') # 'loan_officer' or 'manager'
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    records = db.relationship('AssessmentRecord', backref='officer', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Assessment Record Model
class AssessmentRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    client_name = db.Column(db.String(100), nullable=False)
    client_email = db.Column(db.String(100), nullable=False)
    job_title = db.Column(db.String(100), nullable=False)
    assessment_date = db.Column(db.DateTime, default=datetime.utcnow)
    
    is_self_employed = db.Column(db.Boolean, default=False)
    business_type = db.Column(db.String(50))
    years_in_business = db.Column(db.Integer)
    annual_net_profit_prev_year = db.Column(db.Float)
    annual_net_profit_current_year = db.Column(db.Float)
    
    credit_score = db.Column(db.Integer)
    monthly_income = db.Column(db.Float)
    monthly_debt = db.Column(db.Float)
    property_type = db.Column(db.String(50))
    occupancy_type = db.Column(db.String(50))
    property_value = db.Column(db.Float)
    down_payment = db.Column(db.Float)
    proposed_housing_payment = db.Column(db.Float)
    
    is_eligible_fha = db.Column(db.Boolean)
    is_eligible_conv = db.Column(db.Boolean)
    summary_recommendation = db.Column(db.String(255))
    assessment_notes = db.Column(db.Text)
    tips_provided = db.Column(db.Text)
    
    verification_status = db.Column(db.String(20)) # Verified, Warning, Flagged
    verification_logs = db.Column(db.Text)

# Forms
class RegistrationForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    full_name = StringField('Full Name', validators=[DataRequired()])
    role = SelectField('Role', choices=[('loan_officer', 'Loan Officer'), ('manager', 'Manager')], validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Register')

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

# Initialize database
with app.app_context():
    db.create_all()

# OAuth configuration (Placeholders)
google = oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID', 'placeholder-id'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET', 'placeholder-secret'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)

# Authentication Routes
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(email=form.email.data, full_name=form.full_name.data, role=form.role.data)
        user.set_password(form.password.data)
        db.session.add(user)
        try:
            db.session.commit()
            flash('Account created! You can now log in.', 'success')
            return redirect(url_for('login'))
        except:
            db.session.rollback()
            flash('Email already registered.', 'danger')
    return render_template('register.html', form=form)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('Login failed. Check your email and password.', 'danger')
    return render_template('login.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/login/google')
def google_login():
    redirect_uri = url_for('google_authorize', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/login/google/authorize')
def google_authorize():
    token = google.authorize_access_token()
    user_info = token.get('userinfo')
    if user_info:
        user = User.query.filter_by(email=user_info['email']).first()
        if not user:
            user = User(email=user_info['email'], full_name=user_info.get('name'), google_id=user_info.get('sub'))
            db.session.add(user)
            db.session.commit()
        login_user(user)
    return redirect(url_for('index'))

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    results = None
    tips = None
    inputs = {}
    verification = None
    
    if request.method == 'POST':
        try:
            is_self_employed = request.form.get('is_self_employed') == 'on'
            inputs = {
                'client_name': request.form['client_name'],
                'client_email': request.form['client_email'],
                'job_title': request.form['job_title'],
                'is_self_employed': is_self_employed,
                'credit_score': int(request.form['credit_score']),
                'monthly_debt': float(request.form['monthly_debt']),
                'property_type': request.form['property_type'],
                'occupancy_type': request.form['occupancy_type'],
                'property_value': float(request.form['property_value']),
                'down_payment': float(request.form['down_payment']),
                'proposed_housing_payment': float(request.form['proposed_housing_payment'])
            }
            
            if is_self_employed:
                inputs['business_type'] = request.form['business_type']
                inputs['years_in_business'] = int(request.form['years_in_business'])
                inputs['annual_net_profit_prev_year'] = float(request.form['annual_net_profit_prev_year'])
                inputs['annual_net_profit_current_year'] = float(request.form['annual_net_profit_current_year'])
                income, inc_note = calculate_self_employed_income(inputs)
                inputs['monthly_income'] = income
                results_extra_notes = [inc_note]
            else:
                inputs['monthly_income'] = float(request.form['monthly_income'])
                results_extra_notes = []
                
            results = check_eligibility(inputs)
            results['notes'].extend(results_extra_notes)
            tips, flat_tips = get_officer_tips(results, inputs)

            # Determine Summary Recommendation
            if results['conventional']:
                recommendation = "Approved for Disbursement (Conventional)"
            elif results['fha']:
                recommendation = "Hold for Underwriting Review (FHA)"
            else:
                recommendation = "Disbursement Denied - See Action Plan"
            results['recommendation'] = recommendation
            
            v_status, v_logs = ai_verify({**inputs, **results})
            verification = {"status": v_status, "logs": v_logs}
            
            record = AssessmentRecord(
                user_id=current_user.id,
                client_name=inputs['client_name'], client_email=inputs['client_email'], job_title=inputs['job_title'],
                is_self_employed=is_self_employed,
                business_type=inputs.get('business_type'),
                years_in_business=inputs.get('years_in_business'),
                annual_net_profit_prev_year=inputs.get('annual_net_profit_prev_year'),
                annual_net_profit_current_year=inputs.get('annual_net_profit_current_year'),
                credit_score=inputs['credit_score'], monthly_income=inputs['monthly_income'],
                monthly_debt=inputs['monthly_debt'], property_type=inputs['property_type'],
                occupancy_type=inputs['occupancy_type'], property_value=inputs['property_value'],
                down_payment=inputs['down_payment'], proposed_housing_payment=inputs['proposed_housing_payment'],
                is_eligible_fha=results['fha'], is_eligible_conv=results['conventional'],
                summary_recommendation=recommendation,
                assessment_notes="\n".join(results['notes']), tips_provided=flat_tips,
                verification_status=v_status, verification_logs=v_logs
            )
            db.session.add(record)
            db.session.commit()
            
        except Exception as e:
            results = {"error": f"An error occurred: {str(e)}"}

    return render_template('index.html', results=results, tips=tips, inputs=inputs, verification=verification)

@app.route('/records')
@login_required
def view_records():
    officer_id = request.args.get('officer_id', type=int)
    
    if current_user.role == 'manager':
        query = AssessmentRecord.query
        if officer_id:
            query = query.filter_by(user_id=officer_id)
        records = query.order_by(AssessmentRecord.assessment_date.desc()).all()
        # Get all officers for the filter dropdown
        officers = User.query.filter_by(role='loan_officer').all()
    else:
        records = AssessmentRecord.query.filter_by(user_id=current_user.id).order_by(AssessmentRecord.assessment_date.desc()).all()
        officers = []

    return render_template('records.html', records=records, officers=officers, selected_officer_id=officer_id)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
