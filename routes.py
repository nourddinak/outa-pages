import os
from flask import render_template, request, redirect, url_for, session, flash, abort, send_from_directory
import re
import socket
from werkzeug.utils import secure_filename
from app import app, db
from models import User, HostedPage, Message
from utils import save_uploaded_file, save_uploaded_zip, get_page_url, allowed_file
from datetime import datetime, timedelta

def login_required(f):
    """Decorator to require login"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator to require admin access"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        
        user = User.query.get(session['user_id'])
        if not user or not user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
@login_required
def home():
    return render_template('home.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        user = User.query.filter_by(email=email).first()
        
        if user and user.check_password(password):
            if user.is_blocked:
                flash('Your account has been blocked. Please contact support.', 'error')
                return render_template('login.html')
            
            session['user_id'] = user.id
            session['is_admin'] = user.is_admin
            
            # Redirect to appropriate dashboard
            if user.is_admin:
                return redirect(url_for('superadmin'))
            else:
                return redirect(url_for('host_dashboard'))
        else:
            flash('Invalid email or password.', 'error')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        
        # Validation
        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('register.html')
        
        if User.query.filter_by(email=email).first():
            flash('Email already exists.', 'error')
            return render_template('register.html')
        
        # Create new user
        user = User(email=email)
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()
        
        # Auto-login
        session['user_id'] = user.id
        session['is_admin'] = user.is_admin
        
        flash('Registration successful!', 'success')
        return redirect(url_for('host_dashboard'))
    
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/host')
@login_required
def host_dashboard():
    user = User.query.get(session['user_id'])
    if not user:
        flash('User account not found.', 'error')
        return redirect(url_for('logout'))
    
    if user.is_blocked:
        flash('Your account has been blocked.', 'error')
        return redirect(url_for('logout'))
    
    # Get user's hosted pages
    pages = HostedPage.query.filter_by(user_id=user.id).order_by(HostedPage.created_at.desc()).all()
    
    # Update expired pages
    for page in pages:
        page.update_status()

    server_ip = os.environ.get("SERVER_IP", "127.0.0.1")
    
    return render_template('host.html', user=user, pages=pages, server_ip=server_ip)

@app.route('/upload', methods=['POST'])
@login_required
def upload_page():
    user = User.query.get(session['user_id'])
    
    if not user.can_host_page():
        flash('You have reached your page limit or your account is blocked.', 'error')
        return redirect(url_for('host_dashboard'))
    
    title = request.form.get('title', '').strip()
    if not title:
        flash('Please provide a page title.', 'error')
        return redirect(url_for('host_dashboard'))
    
    # Create new hosted page record
    page = HostedPage(
        user_id=user.id,
        title=title,
        page_id=HostedPage().generate_page_id()
    )
    
    file_path = None
    file = request.files.get('html_file')
    html_content_from_form = request.form.get('html_content', '').strip()

    # Handle file upload
    if file and file.filename:
        if not allowed_file(file.filename) and not file.filename.endswith('.zip'):
            flash('Invalid file type. Please upload an HTML or ZIP file.', 'error')
            return redirect(url_for('host_dashboard'))

        if file.filename.endswith('.zip'):
            file_path = save_uploaded_zip(file, user.id, page.page_id)
        else:
            file_path = save_uploaded_file(file, user.id, page.page_id)
    
    # Handle HTML content
    elif html_content_from_form:
        html_content = request.form['html_content']
        
        # Save HTML content to file
        user_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(user.id))
        os.makedirs(user_dir, exist_ok=True)
        
        page_dir = os.path.join(user_dir, page.page_id)
        os.makedirs(page_dir, exist_ok=True)
        
        file_path = os.path.join(page_dir, 'index.html')
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
    
    if not file_path:
        flash('Please provide either an HTML file or HTML content.', 'error')
        return redirect(url_for('host_dashboard'))
    
    page.file_path = file_path
    db.session.add(page)
    db.session.commit()
    
    flash(f'Page "{title}" uploaded successfully!', 'success')
    return redirect(url_for('host_dashboard'))

def serve_page_content(page):
    """Common function to serve page content"""
    # Check if page is expired
    if page.is_expired():
        page.update_status()
        abort(404)
    
    # Increment view count
    page.views += 1
    db.session.commit()
    
    # Serve the file
    try:
        with open(page.file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except FileNotFoundError:
        abort(404)

@app.route('/page/<page_id>')
def view_page(page_id):
    page = HostedPage.query.filter_by(page_id=page_id).first_or_404()
    return serve_page_content(page)

# Custom domain handling - this should be handled by your web server configuration
# For development/demo purposes, we'll show how it would work
@app.before_request
def handle_custom_domain():
    """Handle requests to custom domains"""
    host = request.headers.get('Host', '').lower()
    
    # Skip if it's the default domain or localhost
    if not host or 'localhost' in host or 'replit' in host or host.startswith('127.0.0.1'):
        return
    
    # Remove port if present
    domain = host.split(':')[0]
    
    # Look for a page with this custom domain
    page = HostedPage.query.filter_by(custom_domain=domain, domain_verified=True).first()
    if page and request.endpoint not in ['serve_page_asset_custom', 'view_custom_domain']:
        # Redirect to the custom domain handler
        return redirect(url_for('view_custom_domain', domain=domain, _external=True, _scheme='https'))

@app.route('/custom/<domain>')
def view_custom_domain(domain):
    """Serve page content for verified custom domains"""
    page = HostedPage.query.filter_by(custom_domain=domain, domain_verified=True).first_or_404()
    return serve_page_content(page)

@app.route('/page/<page_id>/<path:filename>')
def serve_page_asset(page_id, filename):
    """Serve static assets (CSS, JS, images) for hosted pages"""
    page = HostedPage.query.filter_by(page_id=page_id).first_or_404()

    if page.is_expired():
        page.update_status()
        abort(404)

    page_dir = os.path.dirname(page.file_path)
    file_path = os.path.join(page_dir, filename)

    # Security: Ensure the file is within the page's directory
    if os.path.abspath(file_path).startswith(os.path.abspath(page_dir)):
        return send_from_directory(page_dir, filename)

    abort(404)

@app.route('/custom/<domain>/<path:filename>')
def serve_custom_domain_asset(domain, filename):
    """Serve static assets for custom domain pages"""
    page = HostedPage.query.filter_by(custom_domain=domain, domain_verified=True).first_or_404()

    if page.is_expired():
        page.update_status()
        abort(404)

    page_dir = os.path.dirname(page.file_path)
    file_path = os.path.join(page_dir, filename)

    # Security: Ensure the file is within the page's directory
    if os.path.abspath(file_path).startswith(os.path.abspath(page_dir)):
        return send_from_directory(page_dir, filename)

    abort(404)

@app.route('/delete_page/<int:page_id>')
@login_required
def delete_page(page_id):
    user = User.query.get(session['user_id'])
    page = HostedPage.query.filter_by(id=page_id, user_id=user.id).first_or_404()
    
    # Delete files
    if os.path.exists(page.file_path):
        page_dir = os.path.dirname(page.file_path)
        import shutil
        shutil.rmtree(page_dir, ignore_errors=True)
    
    db.session.delete(page)
    db.session.commit()
    
    flash('Page deleted successfully.', 'success')
    return redirect(url_for('host_dashboard'))

@app.route('/admin')
def admin_redirect():
    """Redirect to login if not authenticated, or to superadmin if admin"""
    try:
        if 'user_id' not in session:
            return redirect(url_for('login'))
        
        user = User.query.get(session['user_id'])
        if user and user.is_admin:
            return redirect(url_for('superadmin'))
        else:
            return redirect(url_for('host_dashboard'))
    except Exception as e:
        app.logger.error(f"Error in admin_redirect: {e}")
        return redirect(url_for('login'))

@app.route('/superadmin')
@admin_required
def superadmin():
    try:
        # Search parameters
        user_search = request.args.get('user_search', '').strip()
        message_search = request.args.get('message_search', '').strip()
        page = request.args.get('page', 1, type=int)
        per_page = 20
        
        # User query with search
        user_query = User.query.filter_by(is_admin=False)
        if user_search:
            user_query = user_query.filter(User.email.contains(user_search))
        users = user_query.order_by(User.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        # Message query with search
        message_query = Message.query
        if message_search:
            message_query = message_query.filter(
                (Message.name.contains(message_search)) |
                (Message.email.contains(message_search)) |
                (Message.message.contains(message_search))
            )
        messages = message_query.order_by(Message.timestamp.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        # Statistics
        total_users = User.query.filter_by(is_admin=False).count()
        active_users = User.query.filter_by(is_admin=False, is_blocked=False).count()
        total_pages = HostedPage.query.count()
        blocked_users = User.query.filter_by(is_blocked=True).count()
        
        stats = {
            'total_users': total_users,
            'active_users': active_users,
            'total_pages': total_pages,
            'blocked_users': blocked_users
        }
        
        return render_template('superadmin.html', 
                             users=users, messages=messages, stats=stats,
                             user_search=user_search, message_search=message_search)
    except Exception as e:
        app.logger.error(f"Error in superadmin: {e}")
        flash('An error occurred loading the admin panel. Please try again.', 'error')
        return redirect(url_for('home'))

@app.route('/admin/block_user/<int:user_id>')
@admin_required
def block_user(user_id):
    user = User.query.get_or_404(user_id)
    if not user.is_admin:  # Don't block admin users
        user.is_blocked = True
        db.session.commit()
        flash(f'User {user.email} has been blocked.', 'success')
    return redirect(url_for('superadmin'))

@app.route('/admin/unblock_user/<int:user_id>')
@admin_required
def unblock_user(user_id):
    user = User.query.get_or_404(user_id)
    user.is_blocked = False
    db.session.commit()
    flash(f'User {user.email} has been unblocked.', 'success')
    return redirect(url_for('superadmin'))

@app.route('/admin/edit_user_limit/<int:user_id>', methods=['POST'])
@admin_required
def edit_user_limit(user_id):
    user = User.query.get_or_404(user_id)
    new_limit = request.form.get('page_limit', type=int)
    if new_limit and new_limit > 0:
        user.page_limit = new_limit
        db.session.commit()
        flash(f'Page limit for {user.email} updated to {new_limit}.', 'success')
    else:
        flash('Invalid page limit.', 'error')
    return redirect(url_for('superadmin'))

@app.route('/admin/delete_user/<int:user_id>')
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if not user.is_admin:  # Don't delete admin users
        # Delete user's hosted pages files
        for page in user.hosted_pages:
            if os.path.exists(page.file_path):
                page_dir = os.path.dirname(page.file_path)
                import shutil
                shutil.rmtree(page_dir, ignore_errors=True)
        
        email = user.email
        db.session.delete(user)
        db.session.commit()
        flash(f'User {email} has been deleted.', 'success')
    return redirect(url_for('superadmin'))

@app.route('/admin/delete_message/<int:message_id>')
@admin_required
def delete_message(message_id):
    message = Message.query.get_or_404(message_id)
    db.session.delete(message)
    db.session.commit()
    flash('Message deleted.', 'success')
    return redirect(url_for('superadmin'))

@app.route('/admin/mark_message_read/<int:message_id>')
@admin_required
def mark_message_read(message_id):
    message = Message.query.get_or_404(message_id)
    message.is_read = not message.is_read
    db.session.commit()
    status = 'read' if message.is_read else 'unread'
    flash(f'Message marked as {status}.', 'success')
    return redirect(url_for('superadmin'))

@app.route('/contact', methods=['POST'])
def contact():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    message_text = request.form.get('message', '').strip()
    
    if not all([name, email, message_text]):
        flash('Please fill in all fields.', 'error')
        return redirect(url_for('home') + '#contact')
    
    message = Message(
        name=name,
        email=email,
        message=message_text
    )
    
    db.session.add(message)
    db.session.commit()
    
    flash('Thank you for your message! We will get back to you soon.', 'success')
    return redirect(url_for('home'))

def validate_domain(domain):
    """Validate domain format"""
    if not domain:
        return False
    
    domain = domain.lower().strip()
    
    # Basic domain pattern validation
    domain_pattern = re.compile(
        r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$'
    )
    
    if not domain_pattern.match(domain):
        return False
    
    # Check length
    if len(domain) > 253:
        return False
    
    return True

import dns.resolver

def check_a_record(domain, expected_ip):
    """Check if A record exists and points to the expected IP"""
    try:
        answers = dns.resolver.resolve(domain, 'A')
        for rdata in answers:
            if rdata.address == expected_ip:
                return True
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.exception.Timeout):
        return False
    return False

def check_cname_record(domain, expected_target):
    """Check if CNAME record exists and points to the expected target"""
    try:
        answers = dns.resolver.resolve(domain, 'CNAME')
        for rdata in answers:
            if rdata.target.to_text().strip('.') == expected_target.strip('.'):
                return True
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.exception.Timeout):
        return False
    return False

@app.route('/add_custom_domain', methods=['POST'])
@login_required
def add_custom_domain():
    user = User.query.get(session['user_id'])
    if user.is_blocked:
        flash('Your account is blocked.', 'error')
        return redirect(url_for('host_dashboard'))

    page_id = request.form.get('page_id')
    custom_domain = request.form.get('custom_domain', '').strip().lower()
    verification_method = request.form.get('verification_method', 'CNAME')  # Default to CNAME

    if not page_id or not custom_domain:
        flash('Please select a page and enter a domain name.', 'error')
        return redirect(url_for('host_dashboard'))
    
    # Validate domain format
    if not validate_domain(custom_domain):
        flash('Invalid domain format. Please enter a valid domain name.', 'error')
        return redirect(url_for('host_dashboard'))
    
    # Get the page
    page = HostedPage.query.filter_by(id=page_id, user_id=user.id).first()
    if not page:
        flash('Page not found.', 'error')
        return redirect(url_for('host_dashboard'))
    
    # Check if page can have a domain
    if not page.can_add_domain():
        flash('This page cannot have a custom domain (expired or inactive).', 'error')
        return redirect(url_for('host_dashboard'))
    
    # Check if domain is already used
    existing_domain = HostedPage.query.filter_by(custom_domain=custom_domain).first()
    if existing_domain:
        flash('This domain is already in use by another page.', 'error')
        return redirect(url_for('host_dashboard'))
    
    # Add domain to page
    page.custom_domain = custom_domain
    page.domain_verified = False
    page.domain_added_at = datetime.utcnow()
    page.domain_verification_method = verification_method
    page.generate_domain_verification_token()
    
    db.session.commit()
    
    flash(f'Domain {custom_domain} added! Please add the {verification_method} record to verify ownership.', 'success')
    return redirect(url_for('host_dashboard'))

@app.route('/remove_custom_domain/<int:page_id>', methods=['POST'])
@login_required
def remove_custom_domain(page_id):
    user = User.query.get(session['user_id'])
    if user.is_blocked:
        flash('Your account is blocked.', 'error')
        return redirect(url_for('host_dashboard'))
    
    page = HostedPage.query.filter_by(id=page_id, user_id=user.id).first()
    if not page:
        flash('Page not found.', 'error')
        return redirect(url_for('host_dashboard'))
    
    domain = page.custom_domain
    page.custom_domain = None
    page.domain_verified = False
    page.domain_verification_token = None
    page.domain_added_at = None
    
    db.session.commit()
    
    flash(f'Domain {domain} removed successfully.', 'success')
    return redirect(url_for('host_dashboard'))

@app.route('/verify_domain/<int:page_id>')
@login_required
def verify_domain(page_id):
    user = User.query.get(session['user_id'])
    if user.is_blocked:
        return {'success': False, 'message': 'Account blocked'}

    page = HostedPage.query.filter_by(id=page_id, user_id=user.id).first()
    if not page or not page.custom_domain:
        return {'success': False, 'message': 'Page or domain not found'}

    if page.domain_verification_method == 'A':
        expected_ip = os.environ.get("SERVER_IP", "127.0.0.1")
        if check_a_record(page.custom_domain, expected_ip):
            page.domain_verified = True
            db.session.commit()
            flash('Domain verified successfully!', 'success')
            return {'success': True, 'message': 'A record verified successfully!'}
        else:
            return {'success': False, 'message': f'A record not found or incorrect. Expected {expected_ip}'}
    else:
        expected_target = f"{page.page_id}.outa-host.replit.app"
        if check_cname_record(page.custom_domain, expected_target):
            page.domain_verified = True
            db.session.commit()
            flash('Domain verified successfully!', 'success')
            return {'success': True, 'message': 'CNAME record verified successfully!'}
        else:
            return {'success': False, 'message': f'CNAME record not found or incorrect. Expected {expected_target}'}

@app.route('/builder')
@login_required
def builder():
    return render_template('main.html')

# Create admin user on first run
def create_admin():
    admin = User.query.filter_by(is_admin=True).first()
    if not admin:
        admin = User(
            email='admin@outa.com',
            is_admin=True,
            page_limit=999
        )
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print("Admin user created: admin@outa.com / admin123")

# Call create_admin when the module is loaded
with app.app_context():
    create_admin()
