"""Watch Otsuka's Zenchef reservations for exactly two guests."""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent
NOTIFIED_FILE = ROOT / "notified.json"
BERLIN = ZoneInfo("Europe/Berlin")
PEOPLE = 2
INTERVAL = 1200
NO_AVAILABILITY = re.compile(
    r"No online openings for 2 guests|We are closed on |"
    r"isn.t available online|waitlist only|Next online opening|"
    r"There is no availability with experience"
)

BOOKING_URL = "https://bookings.zenchef.com/results?rid=365906&pid=instagram&lang=en"
# Use the project-local browser installed during setup, if present.
if (ROOT / ".playwright").exists():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))


def select_date(page, day):
    """The selected marker changes only after the widget finishes loading the day."""
    label = f"{day:%A}, {day.day} {day:%B %Y}"
    cell = page.get_by_role("gridcell", name=label, exact=True)
    if not page.get_by_role("gridcell").first.is_visible():
        page.get_by_test_id("calendar-accordion-item").click()
    # A ten-day window can cross a month; the widget may initially jump ahead.
    for _ in range(4):
        middle = page.get_by_role("gridcell").nth(15).get_attribute("aria-label")
        displayed = datetime.strptime(middle, "%A, %d %B %Y").date()
        if (day.year, day.month) == (displayed.year, displayed.month):
            break
        direction = "next" if day > displayed else "previous"
        page.get_by_test_id(f"calendar-{direction}-month-btn").click()
    cell.wait_for(state="visible")
    if cell.get_attribute("aria-disabled") == "true":
        raise RuntimeError(f"Calendar unexpectedly disabled {day}; availability unknown")

    # Zenchef caches the initially loaded day, so a click need not send a request.
    cell.click()
    expect(cell).to_have_attribute("aria-selected", "true")
    expect(page.get_by_test_id("pax-accordion-item")).to_have_text("2 guests")


def check_availability(dates):
    """Return slots only after the UI accepts selection for exactly two guests."""
    found = set()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(locale="en-GB", timezone_id="Europe/Berlin")
            page.set_default_timeout(20000)
            failures = []

            def inspect_response(response):
                url = urlparse(response.url)
                if url.hostname != "bookings-middleware.zenchef.com":
                    return
                if response.status != 200:
                    failures.append(f"Zenchef returned HTTP {response.status}")
                elif url.path == "/getAvailabilities":
                    try:
                        data = response.json()
                        if not isinstance(data, list) or not data or any(
                            not isinstance(entry, dict)
                            or entry.get("date") not in parse_qs(url.query).get("date_begin", [])
                            or not isinstance(entry.get("shifts"), list)
                            for entry in data
                        ):
                            failures.append("Unexpected Zenchef daily response")
                    except Exception:
                        failures.append("Could not read Zenchef daily response")

            # Observe only requests made by the widget; availability comes from UI.
            page.on("response", inspect_response)
            response = page.goto(BOOKING_URL, wait_until="domcontentloaded")
            if response is None or response.status != 200:
                raise RuntimeError("Zenchef page failed to load; availability unknown")
            expect(page).to_have_title("OTSUKA - Online reservation")
            guests = page.get_by_test_id("pax-accordion-item")
            ready = page.get_by_test_id("slot-accordion-item").or_(
                page.get_by_text(NO_AVAILABILITY)
            ).and_(page.locator(":visible"))
            ready.first.wait_for(state="visible")
            if guests.inner_text().strip() != "2 guests":
                guests.click()
                page.get_by_test_id("2guest-btn").click()
            expect(guests).to_have_text("2 guests")
            # Open the calendar and let the normal initial availability load finish.
            calendar = page.get_by_test_id("calendar-accordion-item")
            if not page.get_by_role("gridcell").first.is_visible():
                calendar.click()
            page.get_by_role("gridcell").first.wait_for(state="visible")
            selected = page.locator('[role="gridcell"][aria-selected="true"]')
            initial_label = selected.get_attribute("aria-label")
            # Checking the initial date last makes every click change the selection.
            ordered = sorted(dates, key=lambda day: (
                f"{day:%A}, {day.day} {day:%B %Y}" == initial_label, day
            ))
            for day in ordered:
                select_date(page, day)
                if failures:
                    raise RuntimeError("; ".join(failures) + "; availability unknown")
                expect(page).to_have_title("OTSUKA - Online reservation")
                slot_panel = page.get_by_test_id("slot-accordion-item")
                if slot_panel.is_visible():
                    available = page.locator('button[data-testid^="slot-available-"]')
                    if not available.first.is_visible():
                        slot_panel.click()
                    ids = available.evaluate_all(
                        "els => els.filter(e => e.getClientRects().length).map(e => e.dataset.testid)"
                    )
                    for slot_id in dict.fromkeys(ids):
                        match = re.fullmatch(r"slot-available-(\d{1,2}:\d{2}(?: [AP]M)?)-btn", slot_id)
                        if not match:
                            raise RuntimeError(f"Unrecognized time control: {slot_id}")
                        display_time = match.group(1)
                        time_format = "%I:%M %p" if display_time.endswith("M") else "%H:%M"
                        slot_time = datetime.strptime(display_time, time_format).strftime("%H:%M")
                        if not available.first.is_visible():
                            slot_panel.click()
                        button = page.get_by_test_id(slot_id).first
                        if not button.is_enabled():
                            continue
                        button.click()
                        expect(button).to_have_attribute("data-selected", "true")
                        expect(slot_panel).to_have_text(display_time)
                        expect(guests).to_have_text("2 guests")
                        # Enabled Reserve means the selected slot can continue.
                        # Never click it: Otsuka requires an offer and manual confirmation.
                        expect(page.get_by_role("button", name="Reserve", exact=True)).to_be_enabled()
                        found.add((day.isoformat(), slot_time))
                        logging.info("Confirmed selectable: %s %s for 2", day, slot_time)
                    count = sum(found_day == day.isoformat() for found_day, _ in found)
                    logging.info("%s: %s selectable time(s), all displayed services checked", day, count)
                else:
                    # A completed daily response plus an explicit UI result is required.
                    negative = page.get_by_text(NO_AVAILABILITY)
                    negative.first.wait_for(state="visible")
                    logging.info("%s: no bookable times for 2 (%s)", day, negative.first.inner_text().strip())
        finally:
            browser.close()
    return sorted(found)


def load_notified():
    if not NOTIFIED_FILE.exists():
        return set()
    entries = json.loads(NOTIFIED_FILE.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not all(isinstance(x, str) for x in entries):
        raise ValueError("notified.json must contain a JSON array of slot keys")
    return set(entries)


def save_notified(entries):
    temporary = NOTIFIED_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(sorted(entries), indent=2) + "\n", encoding="utf-8")
    temporary.replace(NOTIFIED_FILE)


def notify(webhook, day, slot_time):
    user_id = os.getenv("DISCORD_USER_ID", "").strip()
    if user_id and not user_id.isdecimal():
        raise RuntimeError("DISCORD_USER_ID must be a numeric Discord user ID")
    mentions = "@everyone" + (f" <@{user_id}>" if user_id else "")
    # Avoid requests' exception text: it can include the secret webhook URL.
    try:
        response = requests.post(
            webhook,
            params={"wait": "true"},
            json={
                "content": (
                    f"{mentions}\n"
                    f"Otsuka Berlin: table for {PEOPLE} selectable on "
                    f"{day} at {slot_time} (Europe/Berlin).\n{BOOKING_URL}"
                ),
                "allowed_mentions": {"parse": ["everyone"], "users": [user_id] if user_id else []},
            },
            timeout=20,
        )
    except requests.RequestException:
        raise RuntimeError("Discord request failed; slot was not marked notified") from None
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Discord returned HTTP {response.status_code}; notification not saved")


def run_check(webhook):
    today = datetime.now(BERLIN).date()
    dates = [today + timedelta(days=offset) for offset in range(30)]
    logging.info("Check: %s through %s, exactly %s people, lunch and dinner", dates[0], dates[-1], PEOPLE)
    notified = load_notified()
    slots = check_availability(dates)
    logging.info("Check complete: %s confirmed selectable time(s)", len(slots))
    for day, slot_time in slots:
        key = f"{day} {slot_time}"
        if key in notified:
            logging.info("Already notified: %s", key)
            continue
        notify(webhook, day, slot_time)
        notified.add(key)
        save_notified(notified)
        logging.info("Discord notification sent: %s", key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run one check and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv(ROOT / ".env")
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        logging.error("Set DISCORD_WEBHOOK_URL in .env")
        return 1
    try:
        while True:
            success = True
            try:
                run_check(webhook)
            except Exception as error:
                logging.error("Check failed: %s", error)
                success = False
            if args.once:
                return 0 if success else 1
            logging.info("Next check in 20 minutes")
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        logging.info("Watcher stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
