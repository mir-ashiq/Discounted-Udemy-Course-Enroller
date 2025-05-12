import os
import subprocess
import sys
import time
import logging
import json
import schedule

print("Starting application...")
def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("app.log"),
            logging.StreamHandler()
        ]
    )
    logging.info("Logging setup complete.")
    logging.info("Starting application...")
    logging.info("Python version: %s", sys.version)
    logging.info("OS: %s", os.name)
    logging.info("Current working directory: %s", os.getcwd())
    #logging.info("Environment variables: %s", json.dumps(dict(os.environ), indent=2))

def run_command(command):
    logging.info("Running command: %s", command)
    try:
        result = subprocess.run(command, shell=True, check=True, text=True, stdout=sys.stdout, stderr=sys.stderr)
        logging.info("Command completed successfully.")
        return result.returncode
    except subprocess.CalledProcessError as e:
        logging.error("Command failed with error: %s", e)
        raise e

def job():
    logging.info("Scheduled job started.")
    run_command("python cli.py")
    logging.info("Scheduled job finished.")

def main():
    setup_logging()

    # Run immediately on startup first
    logging.info("Running initial job on startup...")
    job()
    logging.info("Initial job finished.")

    # Now, schedule the job to run every 4 hours
    schedule.every(4).hours.do(job)
    logging.info("Scheduler started. Next job run is in 4 hours, then every 4 hours thereafter.")
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Application stopped by user.")
    except Exception as e:
        logging.error("An error occurred: %s", str(e))
    finally:
        logging.info("Application exiting.")