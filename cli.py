import threading
import time
import traceback
import sys
from datetime import datetime

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text
from rich import box
from base import VERSION, LoginException, Scraper, Udemy, scraper_dict, logger


console = Console()


def handle_error(error_message, error=None, exit_program=True):
    logger.error(f"ERROR: {error_message}")
    """
    Handle errors consistently throughout the application.

    Args:
        error_message: User-friendly error message
        error: The exception object (optional)
        exit_program: Whether to exit the program after displaying the error (default: True)
    """
    console.print(
        f"\n[bold white on red] ERROR [/bold white on red] [bold red]{error_message}[/bold red]"
    )

    if error:
        error_details = str(error)
        trace = traceback.format_exc()
        console.print(f"[red]Details: {error_details}[/red]")
        console.print("[yellow]Full traceback:[/yellow]")
        console.print(Panel(trace, border_style="red"))

        logger.exception(f"{error_message} - Details: {error_details}")

    if exit_program:
        sys.exit(1)


def create_layout() -> Layout:
    """Create the application layout."""
    layout = Layout(name="root")

    layout.split(
        Layout(name="header", size=3),
        Layout(name="main", ratio=1),
        Layout(name="footer", size=3),
    )

    layout["main"].split(
        Layout(name="stats", size=10),
        Layout(name="course_info", size=14),
    )

    return layout


def create_header() -> Panel:
    """Create the header panel."""
    return Panel(
        f"[bold blue]Discounted Udemy Course Enroller[/bold blue] [cyan]{VERSION}[/cyan] | Logged in as: [bold green]{udemy.display_name}[/bold green] | [yellow]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/yellow]",
        style="white on blue",
    )


def create_footer() -> Panel:
    """Create the footer panel."""

    return Panel(
        "Made with [bold magenta]:heart:[/bold magenta]  by techtanic",
        style="white on dark_blue",
        border_style="bright_blue",
        padding=(0, 2),
    )


def create_stats_panel(udemy: Udemy) -> Panel:
    """Create the statistics panel similar to the GUI version."""

    row1 = Table.grid(padding=3)
    row1.add_column(style="cyan", justify="right", width=22)
    row1.add_column(style="white", justify="left", width=15)
    row1.add_column(style="cyan", justify="right", width=18)
    row1.add_column(style="white", justify="left", width=12)
    row1.add_column(style="cyan", justify="right", width=18)
    row1.add_column(style="white", justify="left", width=12)

    row1.add_row(
        "Successfully Enrolled:",
        f"[green]{udemy.successfully_enrolled_c}[/green]",
        "Already Enrolled:",
        f"[cyan]{udemy.already_enrolled_c}[/cyan]",
        "Expired Courses:",
        f"[red]{udemy.expired_c}[/red]",
    )

    row2 = Table.grid(padding=3)
    row2.add_column(style="cyan", justify="right", width=22)
    row2.add_column(style="white", justify="left", width=15)
    row2.add_column(style="cyan", justify="right", width=18)
    row2.add_column(style="white", justify="left", width=12)
    row2.add_column(style="cyan", justify="right", width=18)
    row2.add_column(style="white", justify="left", width=12)

    row2.add_row(
        "Amount Saved:",
        f"[green]{round(udemy.amount_saved_c, 2)} {udemy.currency.upper()}[/green]",
        "Excluded Courses:",
        f"[yellow]{udemy.excluded_c}[/yellow]",
        "Pending Enrollment:",
        f"[orange1]{len(getattr(udemy, 'valid_courses', []))}/5[/orange1]",
    )

    grid = Table.grid(padding=2)
    grid.add_row(row1)
    grid.add_row(row2)

    return Panel(
        grid,
        title="[bold yellow]Enrollment Stats[/bold yellow]",
        border_style="cyan",
        padding=(2, 2),
    )


def create_course_panel(udemy: Udemy, total_courses: int) -> Panel:
    """Create the current course information panel."""
    if hasattr(udemy, "course") and udemy.course:
        title = udemy.course.title
        url = udemy.course.url
        progress = f"Course {udemy.total_courses_processed} / {total_courses}"
    else:
        title = "No course currently processing"
        url = "N/A"
        progress = "Waiting..."

    table = Table(box=None, show_header=False, show_edge=False, padding=(1, 3))
    table.add_column("", style="cyan", justify="right", width=10)
    table.add_column("", style="white", justify="left")

    table.add_row("Title:", Text(title, style="white", overflow="fold"))
    table.add_row("URL:", Text(url, style="bright_blue", overflow="fold"))
    table.add_row("Progress:", Text(progress, style="yellow"))

    return Panel(
        table,
        title="[bold yellow]Current Course[/bold yellow]",
        border_style="cyan",
        padding=(1, 2),
    )


def create_scraping_thread(site: str):

    code_name = scraper_dict[site]
    task_id = udemy.progress.add_task(site, total=100)
    try:
        threading.Thread(target=getattr(scraper, code_name), daemon=True).start()
        # Wait for the scraper to initialize and set its length
        while getattr(scraper, f"{code_name}_length") == 0:
            time.sleep(0.1)
        
        if getattr(scraper, f"{code_name}_length") == -1:
            # Error occurred during scraper's initialization (e.g., initial fetch failed)
            # Scraper's handle_exception should have set an error message.
            error_msg = getattr(scraper, f"{code_name}_error", f"Initialization error in {site}")
            udemy.progress.update(task_id, description=f"[red]Error: {site}[/red]", completed=100, total=100)
            raise Exception(error_msg) # Will be caught by the except block below

        scraper_total_length = getattr(scraper, f"{code_name}_length")
        udemy.progress.update(task_id, total=scraper_total_length)

        while not getattr(scraper, f"{code_name}_done") and not getattr(
            scraper, f"{code_name}_error"
        ):
            current = getattr(scraper, f"{code_name}_progress")
            udemy.progress.update(
                task_id,
                completed=current,
                total=scraper_total_length,
            )
            time.sleep(0.1)

        if getattr(scraper, f"{code_name}_error"):
            # Error occurred during scraping process
            error_msg = getattr(scraper, f"{code_name}_error", f"Runtime error in {site}")
            udemy.progress.update(task_id, description=f"[red]Error: {site}[/red]", completed=scraper_total_length, total=scraper_total_length)
            raise Exception(error_msg) # Will be caught by the except block below
        else:
            # Successfully completed
            udemy.progress.update(task_id, completed=scraper_total_length)
            logger.debug(
                f"Courses Found {code_name}: {len(getattr(scraper, f'{code_name}_data'))}"
            )

    except Exception:
        error = getattr(scraper, f"{code_name}_error", traceback.format_exc())
        # Ensure the task in progress bar is marked as "finished" but indicates error
        # Use a default total if scraper_total_length wasn't set or was -1
        current_total_for_progress = getattr(scraper, f"{code_name}_length", 100)
        if current_total_for_progress <= 0: current_total_for_progress = 100
        udemy.progress.update(task_id, description=f"[red]Failed: {site}[/red]", completed=current_total_for_progress, total=current_total_for_progress)
        handle_error(f"Error scraping {site}. Continuing with other sites.", error=error, exit_program=False)
        setattr(scraper, f"{code_name}_done", True) # Ensure scraper is marked as done


if __name__ == "__main__":
    try:
        logger.info("Starting CLI application")
        udemy = Udemy("cli")
        udemy.load_settings()
        login_title, main_title = udemy.check_for_update()

        console.print(
            Panel.fit(
                f"[bold blue]Discounted Udemy Course Enroller[/bold blue] [cyan]{VERSION}[/cyan]",
                title="Welcome",
                border_style="cyan",
            )
        )

        if login_title.__contains__("Update"):
            console.print(f"[bold yellow]{login_title}[/bold yellow]")


        login_successful = False
        while not login_successful:
            login_method_attempted = "" # For error messages and logging
            try:
                # Attempt 1: cookies.pkl (via udemy.login() without args)
                login_method_attempted = "cookies.pkl"
                with console.status(f"[cyan]Attempting login via {login_method_attempted}...[/cyan]"):
                    udemy.login()  # Tries to load from cookies.pkl and validate
                console.print(f"[green]Successfully logged in using {login_method_attempted}.[/green]")
                login_successful = True
                continue # Exit while loop

            except LoginException as e_pkl: # cookies.pkl failed (not found or invalid)
                logger.info(f"Login with {login_method_attempted} failed: {e_pkl}")
                console.print(f"[yellow]Login with {login_method_attempted} failed. Trying next method...[/yellow]")

                # Attempt 2: Browser Cookies
                if udemy.settings["use_browser_cookies"]:
                    login_method_attempted = "Browser Cookies"
                    try:
                        with console.status(f"[cyan]Attempting login via {login_method_attempted}...[/cyan]"):
                            udemy.fetch_cookies()      # Fetches cookies from browser into udemy.client.cookies
                            udemy.get_session_info() # Validates the fetched cookies
                        console.print(f"[green]Successfully logged in using {login_method_attempted}.[/green]")
                        login_successful = True
                        continue # Exit while loop
                    except LoginException as e_browser:
                        logger.info(f"Login with {login_method_attempted} failed: {e_browser}")
                        console.print(f"[red]Login with {login_method_attempted} failed. Disabling this option for this session.[/red]")
                        udemy.settings["use_browser_cookies"] = False # Avoid retrying this path
                        # Fall through to saved credentials or manual input

                # Attempt 3: Saved Credentials
                if udemy.settings["email"] and udemy.settings["password"]:
                    login_method_attempted = "Saved Email and Password"
                    try:
                        with console.status(f"[cyan]Attempting login via {login_method_attempted}...[/cyan]"):
                            udemy.login(email=udemy.settings["email"], password=udemy.settings["password"])
                        console.print(f"[green]Successfully logged in using {login_method_attempted}.[/green]")
                        login_successful = True
                        continue # Exit while loop
                    except LoginException as e_saved:
                        logger.info(f"Login with {login_method_attempted} failed: {e_saved}")
                        console.print(f"[red]Login with {login_method_attempted} failed. Clearing saved credentials.[/red]")
                        udemy.settings["email"], udemy.settings["password"] = "", ""
                        # Fall through to manual input

                # Attempt 4: Manual Input
                login_method_attempted = "Manual Input"
                try:
                    email_input = console.input("[cyan]Email: [/cyan]")
                    password_input = console.input("[cyan]Password: [/cyan]")
                    with console.status(f"[cyan]Attempting login via {login_method_attempted}...[/cyan]"):
                        udemy.login(email=email_input, password=password_input)
                    console.print(f"[green]Successfully logged in using {login_method_attempted}.[/green]")
                    if console.input("[cyan]Save credentials for next time? (y/n): [/cyan]").lower() == 'y':
                        udemy.settings["email"], udemy.settings["password"] = email_input, password_input
                    login_successful = True
                    continue # Exit while loop
                except LoginException as e_manual:
                    logger.info(f"Login with {login_method_attempted} failed: {e_manual}")
                    console.print(f"[red]Login with {login_method_attempted} failed: {e_manual}[/red]")
                    if console.input("[yellow]Try manual login again? (y/n): [/yellow]").lower() != 'y':
                        handle_error("Login failed. Exiting.", exit_program=True)
                    # If 'y', loop will repeat and go back to manual input

            except Exception as e_general: # Catch any other unexpected errors during login attempts
                handle_error(f"An unexpected error occurred during login ({login_method_attempted})", error=e_general, exit_program=False)
                # Allow loop to retry, which will likely go to manual input or exit if user chooses
                if console.input("[yellow]Try login process again? (y/n): [/yellow]").lower() != 'y':
                    handle_error("Login failed. Exiting.", exit_program=True)

        # After the loop, if login_successful is True
        udemy.save_settings()
        # Header will be updated with display_name later in the Live context
        console.print(f"[bold green]Login successful. Welcome {udemy.display_name}![/bold green]")
        logger.info(f"Logged in as {udemy.display_name}")

        user_dumb = udemy.is_user_dumb()
        if user_dumb:
            console.print("[bold red]What do you even expect to happen![/bold red]")
            console.print(
                "[yellow]You need to select at least one site, language, and category in the settings.[/yellow]"
            )
            console.input("\nPress Enter to exit...")
            exit()

        scraper = Scraper(udemy.sites)

        console.print(
            "\n[bold cyan]Scraping courses from selected sites...[/bold cyan]"
        )
        logger.info("Scraping courses from selected sites")

        udemy.progress = Progress(
            SpinnerColumn(finished_text="🟢"),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("{task.percentage:.0f}%"),
            TimeRemainingColumn(elapsed_when_finished=True),
        )
        with udemy.progress:
            udemy.scraped_data = scraper.get_scraped_courses(create_scraping_thread)
        total_courses = len(udemy.scraped_data)
        console.print(f"[green]Found {total_courses} courses to process[/green]")

        layout = create_layout()
        layout["header"].update(create_header()) # Header now includes display_name
        layout["footer"].update(create_footer())
        layout["main"]["course_info"].update(create_course_panel(udemy, total_courses))
        layout["main"]["stats"].update(create_stats_panel(udemy))

        udemy.total_courses_processed = 0
        udemy.total_courses = total_courses

        with Live(layout, screen=False, transient=True) as live:

            def update_progress():
                layout["main"]["course_info"].update(
                    create_course_panel(udemy, total_courses)
                )
                layout["main"]["stats"].update(create_stats_panel(udemy))
                live.update(layout)

            udemy.update_progress = update_progress

            try:
                udemy.start_new_enroll()
            except KeyboardInterrupt:
                console.print("[bold yellow]Process interrupted by user[/bold yellow]")
            except Exception as e:
                handle_error(
                    "An unexpected error occurred during enrollment", error=e, exit_program=False
                )
        console.print(
            Panel.fit(f"[bold blue]Enrollment Results[/bold blue]", border_style="cyan")
        )

        table = Table(box=box.ROUNDED)
        table.add_column("Stat", style="cyan")
        table.add_column("Value", style="yellow")

        table.add_row(
            "Successfully Enrolled", f"[green]{udemy.successfully_enrolled_c}[/green]"
        )
        table.add_row(
            "Amount Saved",
            f"[green]{round(udemy.amount_saved_c, 2)} {udemy.currency.upper()}[/green]",
        )
        table.add_row("Already Enrolled", f"[cyan]{udemy.already_enrolled_c}[/cyan]")
        table.add_row("Excluded Courses", f"[yellow]{udemy.excluded_c}[/yellow]")
        table.add_row("Expired Courses", f"[red]{udemy.expired_c}[/red]")

        console.print(table)

    except Exception as e:
        handle_error("A critical error occurred", error=e, exit_program=True)
    #                     udemy.fetch_cookies()
    #                     login_method = "Browser Cookies"
    #             elif udemy.settings["email"] and udemy.settings["password"]:
    #                 email, password = (
    #                     udemy.settings["email"],
    #                     udemy.settings["password"],
    #                 )
    #                 login_method = "Saved Email and Password"
    #             else:
    #                 email = console.input("[cyan]Email: [/cyan]")
    #                 password = console.input("[cyan]Password: [/cyan]")
    #                 login_method = "Email and Password"

    #             logger.info(f"Trying to login using {login_method}")
    #             console.print(f"[cyan]Trying to login using {login_method}...[/cyan]")
    #             # Use the login() method
                
    #             try:
    #                 udemy.login()
    #             except:

    #                 udemy.login(
    #                 email=udemy.settings.get("email"),
    #                 password=udemy.settings.get("password"),
    #             )
    #             if "Email" in login_method:
    #                 with console.status("[cyan]Logging in...[/cyan]"):
    #                     udemy.manual_login(email, password)

    #             with console.status("[cyan]Getting Enrolled Courses...[/cyan]"):
    #                 udemy.get_session_info()

    #             if "Email" in login_method:
    #                 udemy.settings["email"], udemy.settings["password"] = (
    #                     email,
    #                     password,
    #                 )
    #             login_successful = True
    #         except LoginException as e:
    #             handle_error("Login error", error=e, exit_program=False)
    #             if "Browser" in login_method:
    #                 console.print("[red]Can't login using cookies[/red]")
    #                 udemy.settings["use_browser_cookies"] = False
    #             elif "Email" in login_method:
    #                 udemy.settings["email"], udemy.settings["password"] = "", ""

    #     udemy.save_settings()
    #     console.print(f"[bold green]Logged in as {udemy.display_name}[/bold green]")
    #     logger.info(f"Logged in")

    #     user_dumb = udemy.is_user_dumb()
    #     if user_dumb:
    #         console.print("[bold red]What do you even expect to happen![/bold red]")
    #         console.print(
    #             "[yellow]You need to select at least one site, language, and category in the settings.[/yellow]"
    #         )
    #         console.input("\nPress Enter to exit...")
    #         exit()

    #     scraper = Scraper(udemy.sites)

    #     console.print(
    #         "\n[bold cyan]Scraping courses from selected sites...[/bold cyan]"
    #     )
    #     logger.info("Scraping courses from selected sites")

    #     udemy.progress = Progress(
    #         SpinnerColumn(finished_text="🟢"),
    #         TextColumn("[bold blue]{task.description}"),
    #         BarColumn(),
    #         TextColumn("{task.percentage:.0f}%"),
    #         TimeRemainingColumn(elapsed_when_finished=True),
    #     )
    #     with udemy.progress:
    #         udemy.scraped_data = scraper.get_scraped_courses(create_scraping_thread)
    #     total_courses = len(udemy.scraped_data)
    #     console.print(f"[green]Found {total_courses} courses to process[/green]")

    #     layout = create_layout()
    #     layout["header"].update(create_header())
    #     layout["footer"].update(create_footer())
    #     layout["main"]["course_info"].update(create_course_panel(udemy, total_courses))
    #     layout["main"]["stats"].update(create_stats_panel(udemy))

    #     udemy.total_courses_processed = 0
    #     udemy.total_courses = total_courses

    #     with Live(layout, screen=False, transient=True) as live:

    #         def update_progress():
    #             layout["main"]["course_info"].update(
    #                 create_course_panel(udemy, total_courses)
    #             )
    #             layout["main"]["stats"].update(create_stats_panel(udemy))
    #             live.update(layout)

    #         udemy.update_progress = update_progress

    #         try:
    #             udemy.start_new_enroll()
    #         except KeyboardInterrupt:
    #             console.print("[bold yellow]Process interrupted by user[/bold yellow]")
    #         except Exception as e:
    #             handle_error(
    #                 "An unexpected error occurred", error=e, exit_program=False
    #             )
    #     console.print(
    #         Panel.fit(f"[bold blue]Enrollment Results[/bold blue]", border_style="cyan")
    #     )

    #     table = Table(box=box.ROUNDED)
    #     table.add_column("Stat", style="cyan")
    #     table.add_column("Value", style="yellow")

    #     table.add_row(
    #         "Successfully Enrolled", f"[green]{udemy.successfully_enrolled_c}[/green]"
    #     )
    #     table.add_row(
    #         "Amount Saved",
    #         f"[green]{round(udemy.amount_saved_c, 2)} {udemy.currency.upper()}[/green]",
    #     )
    #     table.add_row("Already Enrolled", f"[cyan]{udemy.already_enrolled_c}[/cyan]")
    #     table.add_row("Excluded Courses", f"[yellow]{udemy.excluded_c}[/yellow]")
    #     table.add_row("Expired Courses", f"[red]{udemy.expired_c}[/red]")

    #     console.print(table)

    # except Exception as e:
    #     handle_error("A critical error occurred", error=e, exit_program=True)
