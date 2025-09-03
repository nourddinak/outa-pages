import os
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import current_app
import zipfile
import tempfile

ALLOWED_EXTENSIONS = {'html', 'css', 'js', 'txt', 'md'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_uploaded_file(file, user_id, page_id):
    """Save uploaded file and return the file path"""
    if file and allowed_file(file.filename):
        # Create user directory
        user_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        
        # Create page directory
        page_dir = os.path.join(user_dir, page_id)
        os.makedirs(page_dir, exist_ok=True)
        
        filename = secure_filename(file.filename)
        file_path = os.path.join(page_dir, filename)
        file.save(file_path)
        
        return file_path
    return None

def save_uploaded_zip(file, user_id, page_id):
    """Extract and save uploaded zip file, preserving directory structure."""
    if file and file.filename.endswith('.zip'):
        user_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], str(user_id))
        page_dir = os.path.join(user_dir, page_id)
        os.makedirs(page_dir, exist_ok=True)

        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as temp_file:
            file.save(temp_file.name)

            with zipfile.ZipFile(temp_file.name, 'r') as zip_ref:
                for member in zip_ref.infolist():
                    # Prevent directory traversal attacks
                    target_path = os.path.join(page_dir, member.filename)
                    if os.path.abspath(target_path).startswith(os.path.abspath(page_dir)):
                        if not member.is_dir():
                            zip_ref.extract(member, page_dir)

            os.unlink(temp_file.name)

        # Find the main HTML file (index.html or the first .html file)
        html_files = [f for f in os.listdir(page_dir) if f.endswith('.html')]
        if 'index.html' in html_files:
            return os.path.join(page_dir, 'index.html')
        elif html_files:
            return os.path.join(page_dir, html_files[0])

    return None

def get_page_url(page_id):
    """Generate URL for hosted page"""
    return f"/page/{page_id}"

def cleanup_expired_pages():
    """Clean up expired pages (can be called periodically)"""
    from models import HostedPage
    from app import db
    
    expired_pages = HostedPage.query.filter(
        HostedPage.expiration_date < datetime.utcnow(),
        HostedPage.status == 'Live'
    ).all()
    
    for page in expired_pages:
        page.status = 'Expired'
    
    db.session.commit()
    return len(expired_pages)
