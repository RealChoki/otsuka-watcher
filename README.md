# Otsuka watcher

Checks Otsuka Berlin's actual [Zenchef reservation widget](https://bookings.zenchef.com/results?rid=365906&pid=instagram&lang=en) for **exactly two guests**, then sends new selectable times to Discord.

## Install

Python 3.10 or newer is required. In PowerShell, from this folder:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
```

On macOS/Linux, activate with `source .venv/bin/activate`.

Dependencies and a project-local Chromium are already installed in this workspace. You can run it directly with `.venv\Scripts\python.exe main.py --once`. The application uses `.playwright/` when present; otherwise it uses Playwright's standard browser installation.

## Configure Discord

Copy `.env.example` to `.env` and replace the placeholder:

```dotenv
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN
```

Your supplied webhook is already saved in this workspace's `.env`. Both `.env` and `notified.json` are ignored by Git. Keep the webhook private.

Alerts mention `@everyone`. To also ping a specific person, set `DISCORD_USER_ID`
to their numeric Discord user ID in `.env` and in a GitHub Actions secret with
the same name. Enable Discord Developer Mode and use **Copy User ID** on their
profile. The webhook needs permission to mention everyone in its channel.

## Test one check

```powershell
python main.py --once
```

Checks today and the following 29 dates in **Europe/Berlin**, including every displayed service and time, covering lunch and dinner. It logs each date and sends Discord alerts if new confirmed selectable times appear. A successful check exits 0, including when there are no openings. A failed check exits 1 and reports availability as unknown through an error; it does not treat an error as an empty result.

## Free hosting with GitHub Actions

The public GitHub repository runs `.github/workflows/watch.yml` on standard Linux
runners. It schedules a single check at minutes **07, 27, and 47** of each hour.
Your PC does not need to stay on. GitHub can delay or skip scheduled runs during
high load; this is not a guarantee of exact 20-minute timing.

The webhook is a repository Actions secret named `DISCORD_WEBHOOK_URL`, never a
committed `.env` file. Notification history is restored from `notified.json` on
the separate `watcher-state` branch and saved only when it changes, including
after a failed check that already sent some alerts. Runs cannot overlap. If the
history cannot be read, the run stops instead of sending duplicate alerts.
The history branch contains reservation dates/times and is public with the repo.

Open the repository's **Actions → Watch Otsuka** page for logs. Select **Run
workflow** to check immediately. Disable this workflow to stop cloud checks.
Stop any local watcher with Ctrl+C once the cloud workflow is running; local and
cloud processes have separate copies of notification history.

GitHub disables public-repository schedules after 60 days with no repository
activity. Re-enable the workflow in Actions if that happens. Standard runners in
public repositories are free; private repositories have usage limits.

For a fresh repository, add the source files (excluding `.env` and local runtime
folders), configure the Actions secret, and create a `watcher-state` branch from
`main` containing `notified.json` initialized from your existing history or `[]`.
Then trigger **Run workflow** and verify a successful check before relying on it.

## Run continuously

```powershell
python main.py
```

Runs immediately, then uses `time.sleep(1200)` after each check. Temporary errors are logged and the next scheduled check proceeds. Stop with Ctrl+C. Keep the terminal open and the computer awake. Run one instance to avoid duplicate notifications. No background watcher has been started during setup.

Notifications contain the date, 24-hour Berlin time, and the actual booking URL. Dates and times must still be chosen on the linked page; the link does not invent preselection parameters.

A date/time is saved to `notified.json` after Discord acknowledges delivery. Failed deliveries remain eligible on the next check. A crash between delivery and saving can cause a repeated alert; JSON cannot guarantee exactly-once delivery. Remove an entry to allow another alert for that time.

## How availability is confirmed

The booking URL was supplied from Otsuka's Instagram Reserve button, and its page was verified to identify OTSUKA. The [Zenchef app share link](https://b2c-app.zenchef.com/en/restaurant/otsuka-6cf41d7a-9548-4b94-a506-b3ecbd80fd6b) is also recorded here for reference.

The watcher uses normal Playwright interactions and condition-based waits. It verifies `2 guests`, selects every date through its accessible calendar label, and waits for the calendar's selected marker. That marker changes after the widget finishes loading the date. It also checks HTTP/data errors on browser-generated availability responses; it never calls Zenchef's internal endpoints directly.

Each displayed service is scanned using the widget's actual `slot-available-...-btn` controls. A notification requires an enabled time control, a successful click, the widget's `data-selected` marker, the corresponding time header, two guests, and an enabled Reserve button. Waitlist controls and one-person-only times are excluded. Clickable calendar dates alone never count as availability. Missing controls, unexpected response data, timeouts, or access challenges cause a logged error.

The watcher **never clicks Reserve**, submits a booking, enters personal/payment details, or bypasses CAPTCHAs/anti-bot restrictions. Otsuka states that reservations need a separate confirmation email, so an alert means a time is selectable for a reservation request, not that a reservation has been accepted. Availability can disappear before you open the page.

## Verification on 9 October 2026

A live ten-day check (9–18 October) completed successfully with zero selectable times for two guests. The widget reported no online openings. Positive detection was tested against the real widget UI using isolated controlled availability fixtures: an available two-person slot was recognized, while waitlist and one-person slots produced no alerts. Those fixtures were used only in testing, never in production detection, and sent no Discord messages. Persistent deduplication and failed-delivery behavior were also verified with mocked Discord delivery.

Selectors depend on Zenchef's current widget. If its flow changes, the watcher may report errors and require an update rather than guessing availability.
