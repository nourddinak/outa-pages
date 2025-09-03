from datetime import datetime, timedelta
from app import db
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
import string

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_blocked = db.Column(db.Boolean, default=False)
    page_limit = db.Column(db.Integer, default=5)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    first_sign_in = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship to hosted pages
    hosted_pages = db.relationship('HostedPage', backref='user', lazy=True, cascade='all, delete-orphan')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def can_host_page(self):
        active_pages = HostedPage.query.filter_by(user_id=self.id, status='Live').count()
        return active_pages < self.page_limit and not self.is_blocked
    
    def get_status(self):
        if self.is_blocked:
            return 'Blocked'
        return 'Active'

class HostedPage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    page_id = db.Column(db.String(12), unique=True, nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(20), default='Live')  # Live, Expired
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expiration_date = db.Column(db.DateTime, default=lambda: datetime.utcnow() + timedelta(days=30))
    views = db.Column(db.Integer, default=0)
    
    # Custom domain fields
    custom_domain = db.Column(db.String(255), nullable=True, unique=True)
    domain_verified = db.Column(db.Boolean, default=False)
    domain_verification_token = db.Column(db.String(64), nullable=True)
    domain_added_at = db.Column(db.DateTime, nullable=True)
    domain_verification_method = db.Column(db.String(10), nullable=True)  # 'CNAME' or 'A'
    
    def generate_page_id(self):
        """Generate a unique 12-character page ID"""
        while True:
            page_id = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
            if not HostedPage.query.filter_by(page_id=page_id).first():
                return page_id
    
    def is_expired(self):
        return datetime.utcnow() > self.expiration_date
    
    def update_status(self):
        if self.is_expired() and self.status == 'Live':
            self.status = 'Expired'
            db.session.commit()
    
    def generate_domain_verification_token(self):
        """Generate a verification token for domain ownership"""
        self.domain_verification_token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
        return self.domain_verification_token
    
    def get_page_url(self):
        """Get the URL for this page - custom domain or default"""
        if self.custom_domain and self.domain_verified:
            return f"https://{self.custom_domain}"
        return f"https://{self.page_id}.replit.app"  # Default OUTA URL structure
    
    def can_add_domain(self):
        """Check if a domain can be added to this page"""
        return self.status == 'Live' and not self.is_expired()

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
