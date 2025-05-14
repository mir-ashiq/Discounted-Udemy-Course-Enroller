import os
import json
import threading
import time
import traceback
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.utils import secure_filename

# Assuming base.py, cli.py, and other necessary files are in the same directory or accessible in PYTHONPATH
# We will need to adapt the Udemy and Scraper classes slightly for web use, especially logging.
from base import Udemy, Scraper, LoginException, scraper_dict, VERSION, resource_path, logger as base_logger
from cli import create_layout, create_header, create_footer, create_stats_panel, create_course_panel # For inspiration
import schedule

# --- Flask App Setup ---
app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['UPLOAD_FOLDER'] = 'uploads' # For potential file uploads like settings
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- Global Variables & State Management (Simplified for this example) ---
# In a production app, use a database or more robust state management
udemy_instance = None
scraper_instance = None
enrollment_thread = None
enrollment_logs = []
enrollment_stats = {
    "successfully_enrolled_c": 0,
    "already_enrolled_c": 0,
    "expired_c": 0,
    "excluded_c": 0,
    "amount_saved_c": 0.0,
    "currency": "USD",
    "total_courses_processed": 0,
    "total_courses_to_process": 0,
    "current_course_title": "N/A",
    "current_course_url": "N/A",
    "status": "Idle", # Idle, Scraping, Enrolling, Finished, Error
    "sites_progress": {} # { "site_name": {"current": 0, "total": 0, "done": False, "error": ""} }
}
scheduler_thread = None
next_auto_run_time = None
auto_start_job_instance = None # To store the schedule job object



# --- Logging Adapter for Flask ---
class FlaskLogger:
    def info(self, message):
        enrollment_logs.append(f"[INFO] {message}")
        print(f"[INFO] {message}") # Also print to console for debugging

    def error(self, message):
        enrollment_logs.append(f"[ERROR] {message}")
        print(f"[ERROR] {message}")

    def exception(self, message):
        enrollment_logs.append(f"[EXCEPTION] {message}\n{traceback.format_exc()}")
        print(f"[EXCEPTION] {message}\n{traceback.format_exc()}")
    
    def debug(self, message):
        enrollment_logs.append(f"[DEBUG] {message}")
        print(f"[DEBUG] {message}")

    def success(self, message, color=None): # Adapting from base.py's print
        enrollment_logs.append(f"[SUCCESS] {message}")
        print(f"[SUCCESS] {message}")

flask_logger = FlaskLogger()

# --- Settings Management ---
SETTINGS_FILE_CLI = 'duce-cli-settings.json'
SETTINGS_FILE_GUI = 'duce-gui-settings.json' # We'll use GUI settings as a base for web
DEFAULT_SETTINGS_FILE_GUI = resource_path('default-duce-gui-settings.json')

def load_settings():
    try:
        # Load default settings
        with open(DEFAULT_SETTINGS_FILE_GUI, 'r') as f:
            settings = json.load(f)
    except Exception as e:
        flask_logger.error(f"Critical error loading default settings from {DEFAULT_SETTINGS_FILE_GUI}: {e}")
        # Fallback to a minimal hardcoded structure
        settings = {
            "sites": {key: True for key in scraper_dict.keys()}, "categories": {}, "languages": {},
            "min_rating": 0.0, "course_update_threshold_months": 24, "save_txt": False,
            "discounted_only": False, "instructor_exclude": [], "title_exclude": [],
            "auto_start_enabled": False, "auto_start_hours": 4,
            "stay_logged_in": {"auto": False, "manual": False}, "email": "", "password": ""
        }

    # Load user settings and update defaults
    if os.path.exists(SETTINGS_FILE_GUI):
        try:
            with open(SETTINGS_FILE_GUI, 'r') as f:
                user_settings = json.load(f)
            settings.update(user_settings)
        except Exception as e:
            flask_logger.error(f"Error loading user settings from {SETTINGS_FILE_GUI}, using defaults. Error: {e}")
    else:
        # User settings file doesn't exist, save the current settings (which are defaults)
        save_settings(settings)

    # Ensure new keys are present
    settings.setdefault("auto_start_enabled", False)
    settings.setdefault("auto_start_hours", 4)
    for site_key in scraper_dict.keys(): # Ensure all sites are in settings
        settings["sites"].setdefault(site_key, True)

    # Ensure default categories and languages are present (best effort)
    try:
        with open(DEFAULT_SETTINGS_FILE_GUI, 'r') as f:
            default_s = json.load(f)
        for cat_key in default_s.get("categories", {}).keys():
            settings["categories"].setdefault(cat_key, True)
        for lang_key in default_s.get("languages", {}).keys():
            settings["languages"].setdefault(lang_key, True)
    except Exception: # nosec
        pass

    return settings

def save_settings(settings_data):
    try:
        with open(SETTINGS_FILE_GUI, 'w') as f:
            json.dump(settings_data, f, indent=4)
        return True
    except Exception as e:
        flask_logger.error(f"Error saving settings: {e}")
        return False

# --- Scheduler Functions ---
def auto_enroll_job():
    """Job to be run by the scheduler."""
    global udemy_instance, enrollment_thread, enrollment_stats

    flask_logger.info("Auto-enroll job triggered.")

    if enrollment_thread and enrollment_thread.is_alive():
        flask_logger.info("Auto-enroll: Process already running. Skipping.")
        return

    temp_udemy_instance = Udemy("web", debug=True)
    temp_udemy_instance.logger = flask_logger
    
    user_display_name = None
    user_currency = "USD" 

    try:
        flask_logger.info("Auto-enroll: Attempting login via saved cookies (cookies.pkl)...")
        temp_udemy_instance.load_cookies()
        temp_udemy_instance.get_session_info() 
        user_display_name = temp_udemy_instance.display_name
        user_currency = temp_udemy_instance.currency
        
        udemy_instance = temp_udemy_instance 
        flask_logger.info(f"Auto-enroll: Successfully logged in as {user_display_name} using saved cookies.")

    except LoginException as e:
        flask_logger.error(f"Auto-enroll: LoginException with saved cookies: {e}. Cannot start enrollment.")
        enrollment_stats["status"] = f"Auto-Enroll Error: Login failed ({e})."
        return 
    except Exception as e:
        flask_logger.exception(f"Auto-enroll: Unexpected error during login attempt: {e}")
        enrollment_stats["status"] = f"Auto-Enroll Error: Unexpected login error ({e})."
        return 

    if user_display_name:
        flask_logger.info(f"Auto-enroll: Starting enrollment process for {user_display_name}.")
        enrollment_thread = threading.Thread(target=run_enrollment_process, args=(user_display_name, user_currency))
        enrollment_thread.daemon = True
        enrollment_thread.start()
    else:
        flask_logger.error("Auto-enroll: Could not establish user session. Skipping enrollment.")
        enrollment_stats["status"] = "Auto-Enroll Error: Session could not be established."

def run_scheduler_loop():
    global next_auto_run_time
    flask_logger.info("Scheduler loop started.")
    while True:
        schedule.run_pending()
        if schedule.jobs:
            try:
                next_auto_run_time = schedule.next_run()
            except Exception as e: 
                flask_logger.debug(f"Scheduler: Could not get next_run: {e}")
                next_auto_run_time = None
        else:
            next_auto_run_time = None
        time.sleep(60) 

def start_auto_enroll_scheduler(hours: int):
    global scheduler_thread, auto_start_job_instance
    
    if not isinstance(hours, int) or hours < 1:
        flask_logger.error(f"Invalid auto_start_hours: {hours}. Must be an integer >= 1.")
        return

    stop_auto_enroll_scheduler() 

    flask_logger.info(f"Scheduling auto-enroll job to run every {hours} hours.")
    auto_start_job_instance = schedule.every(hours).hours.do(auto_enroll_job)
    
    if scheduler_thread is None or not scheduler_thread.is_alive():
        scheduler_thread = threading.Thread(target=run_scheduler_loop)
        scheduler_thread.daemon = True
        scheduler_thread.start()
        flask_logger.info("Scheduler thread started.")

def stop_auto_enroll_scheduler():
    global auto_start_job_instance, next_auto_run_time
    if auto_start_job_instance:
        flask_logger.info("Stopping auto-enroll scheduler and clearing job.")
        schedule.cancel_job(auto_start_job_instance)
        auto_start_job_instance = None
    next_auto_run_time = None

# --- Authentication ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    global udemy_instance
    if 'user_display_name' in session:
        return redirect(url_for('index'))

    error = None
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        use_browser_cookies = 'use_browser_cookies' in request.form
        use_saved_cookies = 'use_saved_cookies' in request.form

        try:
            udemy_instance = Udemy("web", debug=True) # Create a new instance for login
            udemy_instance.logger = flask_logger # Use our Flask logger
            
            login_successful = False
            if use_saved_cookies:
                enrollment_logs.append("[INFO] Attempting login via saved cookies (cookies.pkl)...")
                udemy_instance.load_cookies() # Raises LoginException if not found or error
                udemy_instance.get_session_info() # Validates the loaded cookies
                flash('Login successful using saved cookies!', 'success')
                login_successful = True
            elif use_browser_cookies:
                enrollment_logs.append("[INFO] Attempting login via browser cookies...")
                udemy_instance.fetch_cookies()
                udemy_instance.get_session_info()
                flash('Login successful using browser cookies!', 'success')
                login_successful = True
            elif email and password:
                enrollment_logs.append("[INFO] Attempting manual login...")
                udemy_instance.manual_login(email, password)
                udemy_instance.get_session_info() # Validates and sets session info
                # udemy_instance.save_cookies() # Not needed here, manual_login in base.py already saves cookies
                enrollment_logs.append("[INFO] Manual login successful (cookies automatically saved).")
                flash('Login successful and cookies saved!', 'success')
                login_successful = True
            else:
                if email and not password:
                    error = "Password is required for manual login."
                elif not email and password:
                    error = "Email is required for manual login."
                elif not (email or password or use_browser_cookies or use_saved_cookies): # All empty or only one cookie option
                    error = "Please select a login method or provide full credentials."
                # If error is still None here, it implies an unhandled case or a checkbox was selected
                # but failed validation before this point (which is unlikely with current structure).

            if login_successful:
                session['user_display_name'] = udemy_instance.display_name
                session['user_currency'] = udemy_instance.currency
                return redirect(url_for('index'))
            # If not login_successful, 'error' should be set (either by LoginException or the 'else' block above)

        except LoginException as e:
            error_msg = str(e)
            if "cookies.pkl not found" in error_msg and use_saved_cookies:
                error = "Saved cookies (cookies.pkl) not found. Please login manually first to create it, or use another login method."
            else:
                error = error_msg
            flask_logger.error(f"LoginException: {error}")
            udemy_instance = None # Clear instance on login failure
        except Exception as e:
            error = f"An unexpected error occurred: {str(e)}"
            flask_logger.exception("Unexpected login error")
            udemy_instance = None

    return render_template('login.html', error=error, version=VERSION)

@app.route('/logout')
def logout():
    global udemy_instance
    session.clear()
    udemy_instance = None # Clear the instance
    enrollment_logs.clear()
    reset_enrollment_stats()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

# --- Main Application Routes ---
@app.route('/')
def index():
    if 'user_display_name' not in session:
        return redirect(url_for('login'))
    
    settings = load_settings()
    # Make sure udemy_instance is available if logged in
    global udemy_instance
    if not udemy_instance and 'user_display_name' in session:
        # This might happen if server restarts, try to re-establish session
        # For now, we'll just ensure it's created for settings access
        udemy_instance = Udemy("web", debug=True)
        udemy_instance.logger = flask_logger
        udemy_instance.display_name = session['user_display_name']
        udemy_instance.currency = session.get('user_currency', 'USD')
        # A more robust solution would re-validate cookies or prompt re-login

    return render_template('index.html', 
                           user_display_name=session['user_display_name'],
                           version=VERSION,
                           settings=settings,
                           scraper_dict=scraper_dict,
                           enrollment_stats=enrollment_stats,
                           logs=enrollment_logs)

@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    if 'user_display_name' not in session:
        return redirect(url_for('login'))

    settings_data = load_settings()
    if request.method == 'POST':
        # Update sites
        for site_key in scraper_dict.keys():
            settings_data["sites"][site_key] = site_key in request.form
        
        # Update categories
        for cat_key in settings_data["categories"].keys():
            settings_data["categories"][cat_key] = cat_key in request.form

        # Update languages
        for lang_key in settings_data["languages"].keys():
            settings_data["languages"][lang_key] = lang_key in request.form
            
        settings_data["min_rating"] = float(request.form.get('min_rating', 0.0))
        settings_data["course_update_threshold_months"] = int(request.form.get('course_update_threshold_months', 24))
        settings_data["save_txt"] = 'save_txt' in request.form
        settings_data["discounted_only"] = 'discounted_only' in request.form
        
        # Exclusions (handle multiline text to list)
        settings_data["instructor_exclude"] = [line.strip() for line in request.form.get('instructor_exclude', '').splitlines() if line.strip()]
        settings_data["title_exclude"] = [line.strip() for line in request.form.get('title_exclude', '').splitlines() if line.strip()]
        
        settings_data["auto_start_enabled"] = 'auto_start_enabled' in request.form
        settings_data["auto_start_hours"] = int(request.form.get('auto_start_hours', 4))


        if save_settings(settings_data):
            flash('Settings saved successfully!', 'success')
        else:
            flash('Error saving settings.', 'danger')
        return redirect(url_for('settings_page'))
        # Update scheduler based on new settings
        if settings_data["auto_start_enabled"]:
            start_auto_enroll_scheduler(settings_data["auto_start_hours"])
        else:
            stop_auto_enroll_scheduler()

    return render_template('settings.html', 
                           settings=settings_data, 
                           scraper_dict=scraper_dict,
                           version=VERSION)

# --- Enrollment Process ---
def reset_enrollment_stats(user_currency_from_session: str = "USD"):
    global enrollment_stats, enrollment_logs
    enrollment_logs.clear()
    enrollment_stats = {
        "successfully_enrolled_c": 0, "already_enrolled_c": 0, "expired_c": 0,
        "excluded_c": 0, "amount_saved_c": 0.0, "currency": user_currency_from_session,
        "total_courses_processed": 0, "total_courses_to_process": 0,
        "current_course_title": "N/A", "current_course_url": "N/A",
        "status": "Idle", "sites_progress": {} # Reset sites_progress
    }

def update_gui_stats():
    """Updates the global enrollment_stats dictionary from the udemy_instance."""
    global udemy_instance, enrollment_stats
    if udemy_instance:
        enrollment_stats["successfully_enrolled_c"] = udemy_instance.successfully_enrolled_c
        enrollment_stats["already_enrolled_c"] = udemy_instance.already_enrolled_c
        enrollment_stats["expired_c"] = udemy_instance.expired_c
        enrollment_stats["excluded_c"] = udemy_instance.excluded_c
        enrollment_stats["amount_saved_c"] = float(udemy_instance.amount_saved_c) # Ensure float for JSON
        enrollment_stats["currency"] = udemy_instance.currency
        enrollment_stats["total_courses_processed"] = getattr(udemy_instance, 'total_courses_processed', 0)
        
        if hasattr(udemy_instance, 'course') and udemy_instance.course:
            enrollment_stats["current_course_title"] = udemy_instance.course.title
            enrollment_stats["current_course_url"] = udemy_instance.course.url
        
        if hasattr(udemy_instance, 'valid_courses'):
             enrollment_stats["pending_enrollment"] = f"{len(udemy_instance.valid_courses)}/5"


def create_web_scraping_thread(site_key: str, user_currency: str):
    """
    Starts the scraping method for a given site in a new thread and monitors its progress,
    updating the global enrollment_stats["sites_progress"].
    This function is intended to be the 'target' for Scraper.get_scraped_courses.
    """
    global scraper_instance, enrollment_stats
    code_name = scraper_dict[site_key]
    
    # Ensure progress entry exists and set initial status
    prog_entry = enrollment_stats["sites_progress"].setdefault(site_key, {})
    prog_entry.update({"current": 0, "total": 0, "done": False, "error": "", "status": "Initializing"})

    try:
        flask_logger.debug(f"Starting scraper method for {site_key} in a new thread.")
        # Start the actual scraper method (e.g., scraper_instance.rd())
        scraper_method_thread = threading.Thread(target=getattr(scraper_instance, code_name), daemon=True)
        scraper_method_thread.start()

        # Wait for the scraper to initialize and set its length or an error
        flask_logger.debug(f"Monitoring {site_key}: Waiting for _length or _error.")
        while getattr(scraper_instance, f"{code_name}_length", 0) == 0 and \
              not getattr(scraper_instance, f"{code_name}_error", ""):
            if not scraper_method_thread.is_alive() and not getattr(scraper_instance, f"{code_name}_done", False):
                time.sleep(0.5) # Give a moment for scraper's error handler to set flags
                if getattr(scraper_instance, f"{code_name}_length", 0) == 0 and \
                   not getattr(scraper_instance, f"{code_name}_error", "") and \
                   not getattr(scraper_instance, f"{code_name}_done", False):
                    flask_logger.error(f"Scraper thread for {site_key} died prematurely before setting length/error/done.")
                    setattr(scraper_instance, f"{code_name}_error", "Scraper thread died unexpectedly during init.")
                    setattr(scraper_instance, f"{code_name}_length", -1) # Signal init error
                    setattr(scraper_instance, f"{code_name}_done", True)
                break # Exit while loop to process the error state
            time.sleep(0.1)
        
        scraper_total_length = getattr(scraper_instance, f"{code_name}_length", 0)
        flask_logger.debug(f"Monitoring {site_key}: _length={scraper_total_length}, _error='{getattr(scraper_instance, f'{code_name}_error', '')}'")

        if scraper_total_length == -1: # Error occurred during scraper's initialization
            error_msg = getattr(scraper_instance, f"{code_name}_error", f"Initialization error in {site_key}")
            prog_entry.update({"error": error_msg, "total": 0, "status": "Error"}) # Total 0 for error bar
            flask_logger.error(f"Scraper init error for {site_key}: {error_msg}")
        else:
            prog_entry.update({"total": scraper_total_length, "status": "Scraping..."})
            flask_logger.debug(f"Monitoring {site_key}: Now scraping, total={scraper_total_length}.")

            while not getattr(scraper_instance, f"{code_name}_done", False) and \
                  not getattr(scraper_instance, f"{code_name}_error", ""):
                if not scraper_method_thread.is_alive() and not getattr(scraper_instance, f"{code_name}_done", False):
                    time.sleep(0.5) # Give a moment for flags
                    if not getattr(scraper_instance, f"{code_name}_done", False) and \
                       not getattr(scraper_instance, f"{code_name}_error", ""):
                        flask_logger.error(f"Scraper thread for {site_key} died prematurely during scraping.")
                        setattr(scraper_instance, f"{code_name}_error", "Scraper thread died unexpectedly during scraping.")
                        setattr(scraper_instance, f"{code_name}_done", True) # Mark as done to exit loop
                    break # Exit while loop
                
                current = getattr(scraper_instance, f"{code_name}_progress", 0)
                prog_entry["current"] = current
                time.sleep(0.1) # Poll interval
            
            # After the loop, check for errors or completion
            if getattr(scraper_instance, f"{code_name}_error", ""):
                error_msg = getattr(scraper_instance, f"{code_name}_error", f"Runtime error in {site_key}")
                prog_entry.update({"error": error_msg, "current": scraper_total_length, "status": "Error"})
                flask_logger.error(f"Scraper runtime error for {site_key}: {error_msg}")
            elif getattr(scraper_instance, f"{code_name}_done", False): # Check done flag
                prog_entry.update({"current": scraper_total_length, "status": "Completed"})
                flask_logger.info(f"Scraping completed for {site_key}. Courses: {len(getattr(scraper_instance, f'{code_name}_data', []))}")
            else: # Should not happen if loops are correct and scraper sets flags
                prog_entry.update({"error": "Unknown scraping state", "status": "Error"})
                flask_logger.error(f"Unknown scraping state for {site_key} after monitoring loop.")

    except Exception as e: # Catches errors in the MONITORING logic itself
        tb_str = traceback.format_exc()
        error_msg = f"Monitor error for {site_key}: {str(e)}"
        flask_logger.exception(f"Error in web scraping thread for {site_key}: {tb_str}")
        prog_entry.update({"error": error_msg, "status": "Error"})
        if prog_entry.get("total", 0) <= 0: prog_entry["total"] = 1 # Avoid division by zero if total is 0
        prog_entry["current"] = prog_entry["total"] # Show full error bar
    finally:
        prog_entry["done"] = True
        if prog_entry["status"] not in ["Completed", "Error"] and not prog_entry["error"]:
            prog_entry.update({"error": "Monitor finished inconclusively", "status": "Error"})
        flask_logger.debug(f"Monitoring thread for {site_key} finished with status: {prog_entry['status']}.")

def run_enrollment_process(user_display_name_from_session: str, user_currency_from_session: str):
    global udemy_instance, scraper_instance, enrollment_stats, enrollment_thread
    
    if not user_display_name_from_session: # Check passed argument
        enrollment_stats["status"] = "Error: Not logged in"
        return

    # Determine currency to use
    current_user_currency = 'USD' # Default
    if udemy_instance and hasattr(udemy_instance, 'currency') and udemy_instance.currency:
        current_user_currency = udemy_instance.currency
    elif user_currency_from_session:
        current_user_currency = user_currency_from_session
    
    reset_enrollment_stats(current_user_currency)
    enrollment_stats["status"] = "Initializing..." # Set status after reset
    
    try:
        # Re-initialize or ensure udemy_instance is valid and logged in
        # This is crucial. For simplicity, we assume udemy_instance is kept alive by session or re-created.
        # If not, it needs to be re-authenticated.
        if not udemy_instance or not udemy_instance.display_name: # Basic check
            # Attempt to re-login using saved cookies if available (conceptual)
            # This part needs robust handling in a real app.
            # For now, assume login must be active.
            flask_logger.info("Re-initializing Udemy instance for enrollment process.")
            udemy_instance = Udemy("web", debug=True)
            udemy_instance.logger = flask_logger
            
            # Attempt to load cookies (e.g., from a saved file or session)
            try:
                udemy_instance.load_cookies() # Assumes cookies.pkl exists from a previous login
                udemy_instance.get_session_info()
                current_user_currency = udemy_instance.currency # Update currency from loaded session
                enrollment_stats["currency"] = current_user_currency # Reflect in stats
            except Exception as login_e:
                enrollment_stats["status"] = f"Error: Session expired or invalid. Please re-login. ({login_e})"
                flask_logger.error(f"Failed to re-initialize session: {login_e}")
                return
        else: # udemy_instance exists, ensure its currency is used
            if hasattr(udemy_instance, 'currency') and udemy_instance.currency:
                 current_user_currency = udemy_instance.currency
                 enrollment_stats["currency"] = current_user_currency

        settings = load_settings()
        udemy_instance.settings = settings # Pass current settings to the instance
        udemy_instance.display_name = user_display_name_from_session # Use passed value
        udemy_instance.currency = current_user_currency # Ensure instance has the correct currency
        udemy_instance.is_user_dumb() # This loads settings into udemy_instance attributes like sites, categories etc.

        if not udemy_instance.sites:
            # is_user_dumb populates udemy_instance.sites based on settings.
            # If still no sites, then it's an error.
            enrollment_stats["status"] = "Error: No sites selected in settings."
            flask_logger.error("No sites selected for scraping.")
            return

        scraper_instance = Scraper(udemy_instance.sites, debug=True)
        udemy_instance.update_progress = update_gui_stats # Hook for stats update

        enrollment_stats["status"] = "Scraping courses..."
        flask_logger.info("Starting course scraping...")
        
        # Initialize sites_progress for all selected sites before starting scraping
        for site_key_init in udemy_instance.sites:
            enrollment_stats["sites_progress"][site_key_init] = {
                "current": 0, "total": 0, "done": False, "error": "", "status": "Pending"
            }

        # The target lambda now correctly passes the determined currency.
        # create_web_scraping_thread itself doesn't use user_currency argument directly for its logic,
        # but it's passed for consistency if it were needed.
        udemy_instance.scraped_data = scraper_instance.get_scraped_courses(target=lambda site_key: create_web_scraping_thread(site_key, current_user_currency))
        enrollment_stats["total_courses_to_process"] = len(udemy_instance.scraped_data)
        flask_logger.info(f"Scraping finished. Found {enrollment_stats['total_courses_to_process']} unique courses.")

        enrollment_stats["status"] = "Enrolling courses..."
        flask_logger.info("Starting enrollment process...")
        
        udemy_instance.start_new_enroll() # This method contains the main enrollment loop

        enrollment_stats["status"] = "Finished"
        flask_logger.info("Enrollment process completed.")

    except LoginException as e:
        enrollment_stats["status"] = f"Login Error: {e}"
        flask_logger.error(f"LoginException during enrollment: {e}")
    except Exception as e:
        tb_str = traceback.format_exc()
        enrollment_stats["status"] = f"Error: {e}"
        flask_logger.exception(f"Exception during enrollment process: {tb_str}")
    finally:
        update_gui_stats() # Final update
        enrollment_thread = None # Clear thread

@app.route('/start_enrollment', methods=['POST'])
def start_enrollment():
    global enrollment_thread
    if 'user_display_name' not in session:
        return jsonify({"status": "error", "message": "Not logged in"}), 401
    
    if enrollment_thread and enrollment_thread.is_alive():
        return jsonify({"status": "error", "message": "Process already running"}), 400

    user_display_name = session.get('user_display_name')
    user_currency = session.get('user_currency', 'USD')

    enrollment_thread = threading.Thread(target=run_enrollment_process, args=(user_display_name, user_currency))
    enrollment_thread.daemon = True
    enrollment_thread.start()
    return jsonify({"status": "success", "message": "Enrollment process started"})

@app.route('/get_status')
def get_status():
    if 'user_display_name' not in session:
        return jsonify({"status": "error", "message": "Not logged in"}), 401
    
    # Prune logs if they get too long for web display
    MAX_LOGS = 200
    if len(enrollment_logs) > MAX_LOGS:
        pruned_logs = enrollment_logs[-MAX_LOGS:]
    else:
        pruned_logs = enrollment_logs
        
    auto_start_status_msg = "Disabled"
    next_run_str = "N/A"
    current_settings = load_settings()

    if current_settings.get("auto_start_enabled"):
        if next_auto_run_time:
            try:
                next_run_str = next_auto_run_time.strftime("%Y-%m-%d %H:%M:%S")
                auto_start_status_msg = f"Enabled, next run: {next_run_str}"
            except AttributeError: 
                next_run_str = "Pending schedule..."
                auto_start_status_msg = f"Enabled, next run: {next_run_str}"
        elif schedule.jobs:
            next_run_str = "Calculating..."
            auto_start_status_msg = f"Enabled, next run: {next_run_str}"
        else:
            auto_start_status_msg = "Enabled, waiting for next schedule..."
            
    return jsonify({
        "stats": enrollment_stats,
        "logs": pruned_logs,
        "process_running": enrollment_thread is not None and enrollment_thread.is_alive(),
        "auto_start_status": auto_start_status_msg,
    })


# --- Error Handling ---
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    flask_logger.exception("Internal Server Error")
    return render_template('500.html', error=str(e)), 500

# --- Initial App Setup ---
def initial_app_setup():
    app_settings = load_settings()
    if app_settings.get("auto_start_enabled", False):
        hours = app_settings.get("auto_start_hours", 4)
        if isinstance(hours, int) and hours >=1:
             start_auto_enroll_scheduler(hours)
        else:
            flask_logger.error(f"Invalid auto_start_hours ({hours}) in settings. Auto-start disabled.")

if __name__ == '__main__':
    # For development, using Flask's built-in server.
    # For production, use a proper WSGI server like Gunicorn or Waitress.
    initial_app_setup()
    app.run(debug=True, host='0.0.0.0', port=5001)
