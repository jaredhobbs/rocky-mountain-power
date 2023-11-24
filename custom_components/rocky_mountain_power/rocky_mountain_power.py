"""Implementation of Rocky Mountain Power API."""
import atexit
import locale
import os.path
import sys
import dataclasses
from datetime import date, datetime, timedelta
from enum import Enum
import json
import logging
from typing import Any, Optional

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException

_LOGGER = logging.getLogger(__file__)
DEBUG_LOG_RESPONSE = False
locale.setlocale(locale.LC_ALL, "en_US")


class RockyMountainPowerUtility:
    LOGIN_URL = "https://csapps.rockymountainpower.net/idm/login"

    def __init__(self, selenium_host: str = "localhost"):
        self.selenium_host: str = selenium_host
        self.user_id = None
        self.account = {}
        self.forecast = {}
        self.xhrs = {}

    def on_quit(self, *args, **kwargs):
        try:
            self.br.close()
            self.br.quit()
        except:
            pass

    def get_el(self, by, val, keys=None, multi=False, required=True, text=None):
        func = self.br.find_element
        if multi or text:
            func = self.br.find_elements
        try:
            el = func(by, val)
            if keys:
                for k in keys:
                    el.send_keys(k)
            if text:
                el = [e for e in el if e.text == text][0]
            return el
        except:
            if required:
                raise
            return [] if multi else None

    def click(self, els):
        for el in els:
            try:
                el.click()
                return
            except:
                pass

    def find_el(self, selectors):
        for by, selector in selectors:
            target = self.get_el(by, selector, required=False)
            if target is not None and target.is_displayed():
                return target

    def init_browser(self):
        options = webdriver.ChromeOptions()
        options.enable_downloads = True
        options.add_argument("--disable-extensions")
        options.add_argument("--mute-audio")
        prefs = {
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "profile.default_content_settings": {
                "images": 2,
            },
        }
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        options.add_experimental_option("prefs", prefs)

        self.br = webdriver.Remote(
            command_executor=f"http://{self.selenium_host}:4444",
            options=options,
        )
        atexit.register(self.on_quit)
        sys.excepthook = self.on_quit
        self.br.implicitly_wait(10)
        self.wait = WebDriverWait(self.br, 10)

    def log_filter(self, log):
        return (
            # is an actual response
            log["method"] == "Network.responseReceived"
            # and json
            and "json" in log["params"]["response"]["mimeType"]
        )

    def send(self, cmd, params=None):
        resource = f"/session/{self.br.session_id}/chromium/send_command_and_get_result"
        url = self.br.command_executor._url + resource
        body = json.dumps({"cmd": cmd, "params": params or {}})
        response = self.br.command_executor._request("POST", url, body)
        return response.get("value")

    def get_xhrs(self):
        logs_raw = self.br.get_log("performance")
        logs = [json.loads(lr["message"])["message"] for lr in logs_raw]
        xhrs = {}
        for log in filter(self.log_filter, logs):
            resp_url = log["params"]["response"]["url"]
            request_id = log["params"]["requestId"]
            try:
                xhrs[resp_url] = self.send("Network.getResponseBody", {"requestId": request_id})["body"]
            except:
                pass
        self.xhrs = {
            **self.xhrs,
            **xhrs,
        }
        return self.xhrs

    def login(self, username, password):
        self.init_browser()
        self.br.get(self.LOGIN_URL)
        try:
            self.wait.until(EC.title_is("Sign in"))
        except:
            raise CannotConnect
        self.br.fullscreen_window()
        target = self.get_el(By.CSS_SELECTOR, "wcss-cookie-banner>aside>button", required=False)
        if target and target.is_displayed():
            target.click()
        self.wait.until(
            EC.frame_to_be_available_and_switch_to_it(
                (
                    By.CSS_SELECTOR,
                    "iframe#loginframe",
                )
            )
        )
        self.get_el(
            By.CSS_SELECTOR,
            "input#signInName",
            keys=username,
        )
        self.get_el(
            By.CSS_SELECTOR,
            "input#password",
            keys=password,
        )
        target = self.get_el(By.CSS_SELECTOR, "button#next")
        target.click()
        try:
            self.wait.until(EC.title_is("My account"))
        except:
            raise InvalidAuth

        xhrs = self.get_xhrs()
        me = json.loads(xhrs["https://csapps.rockymountainpower.net/api/user/me"])
        self.user_id = me["id"]
        accounts = json.loads(xhrs["https://csapps.rockymountainpower.net/api/self-service/getAccountList"])
        self.account = accounts["getAccountListResponseBody"]["accountList"]["webAccount"][0]
        return xhrs

    def goto_energy_usage(self):
        target = self.get_el(By.LINK_TEXT, "Energy usage")
        target.click()
        try:
            self.wait.until(EC.title_is("Energy usage"))
        except:
            raise CannotConnect

    def get_forecast(self):
        self.goto_energy_usage()

        xhrs = self.get_xhrs()
        details = json.loads(xhrs["https://csapps.rockymountainpower.net/api/energy-usage/getMeterType"])
        # {
        #   "isAMIMeter":true,
        #   "businessUnitCode":"11441",
        #   "isAcctAMIEligible":true,
        #   "displayInvoicedUsage":false,
        #   "minDailyUsageDate":"2022-03-21",
        #   "maxDailyUsageDate":"2023-11-22",
        #   "startDateForAMIAcctView":"2023-11-14",
        #   "endDateForAMIAcctView":"2023-11-22",
        #   "operationResult":{
        #     "returnStatus":1
        #   },
        #   "highBillAlertValue":"200",
        #   "projectedCost":"170",
        #   "projectedCostHigh":"195",
        #   "projectedCostLow":"144",
        #   "noDaysIntoBillingCycle":9,
        #   "isNetMeterFlag":false
        # }
        self.forecast = details.get("getMeterTypeResponseBody", {})
        return self.forecast

    def get_usage_by_month(self):
        xhr_url = "https://csapps.rockymountainpower.net/api/account/getUsageHistoryAndGraphDataV1"
        self.goto_energy_usage()
        target = self.get_el(By.CSS_SELECTOR, "div.mat-form-field-infix", multi=True)
        target[3].click()
        # Selects monthly data for the past 2 years
        target = self.get_el(By.CSS_SELECTOR, ".mat-option", multi=True)[0]
        target.click()
        self.wait.until(lambda _: xhr_url in self.get_xhrs())
        xhrs = self.get_xhrs()
        details = json.loads(xhrs[xhr_url])
        usage = []
        # {
        #   "usagePeriod":"Oct 2021",
        #   "usagePeriodEndDate":"2021-10-12",
        #   "invoiceAmount":"$143",
        #   "elapsedDays":29,
        #   "kwhUsageQuantity":1124.0,
        #   "kwhReverseUsageQuantity":"0.0",
        #   "onkwhUsageQuantity":"0.0",
        #   "offkwhUsageQuantity":"0.0",
        #   "invoicedUsage":"1124",
        #   "missingDataFlag":"N",
        #   "avgTemperature":"62.78"
        # },
        for d in details.get("getUsageHistoryAndGraphDataV1ResponseBody", {}).get("usageHistory", {}).get("usageHistoryLineItem", []):
            end_time = datetime.fromisoformat(d["usagePeriodEndDate"])
            start_time = end_time - timedelta(days=int(d["elapsedDays"]))
            amount = None
            try:
                amount = locale.atof(d.get("invoiceAmount", "").strip("$")) or None
            except ValueError:
                pass
            usage.append({
                "startTime": start_time,
                "endTime": end_time,
                "usage": float(d.get("kwhUsageQuantity", 0)),
                "amount": amount,
            })
        return usage

    def get_usage_by_day(self, months=1):
        xhr_url = "https://csapps.rockymountainpower.net/api/energy-usage/getUsageForDateRange"
        self.goto_energy_usage()
        target = self.get_el(By.CSS_SELECTOR, "div.mat-form-field-infix", multi=True)
        target[3].click()
        # Selects daily data for the past month
        target = self.get_el(By.CSS_SELECTOR, ".mat-option", multi=True)[2]
        target.click()
        self.wait.until(lambda _: xhr_url in self.get_xhrs())
        usage = []
        while months > 0:
            xhrs = self.get_xhrs()
            details = json.loads(xhrs[xhr_url])
            # {
            #   "usagePeriodEndDate":"2023-10-24",
            #   "dollerAmount":"$5",
            #   "numberOfDays":1,
            #   "kwhUsageQuantity":"37.85",
            #   "kwhReverseUsageQuantity":"0.00",
            #   "avgTemperature":"56.5",
            #   "missingDataFlag":"N",
            #   "displayDollarAmount":"Y"
            # },
            for d in details.get("getUsageForDateRangeResponseBody", {}).get("dailyUsageList", {}).get("usgHistoryLineItem", []):
                end_time = datetime.fromisoformat(d["usagePeriodEndDate"])
                start_time = end_time - timedelta(days=1)
                amount = None
                try:
                    amount = locale.atof(d.get("dollerAmount", "").strip("$")) or None
                except ValueError:
                    pass
                usage.append({
                    "startTime": start_time,
                    "endTime": end_time,
                    "usage": float(d.get("kwhUsageQuantity", 0)),
                    "amount": amount,
                })
            months -= 1
            if months > 0:
                self.xhrs = {}
                target = self.get_el(By.CSS_SELECTOR, "button.link", text="PREVIOUS")
                try:
                    target.click()
                except ElementClickInterceptedException:
                    break
                try:
                    self.wait.until(lambda _: xhr_url in self.get_xhrs())
                except TimeoutException:
                    break
        return usage

    def get_usage_by_hour(self, days=1):
        xhr_url = "https://csapps.rockymountainpower.net/api/energy-usage/getIntervalUsageForDate"
        self.goto_energy_usage()
        target = self.get_el(By.CSS_SELECTOR, "div.mat-form-field-infix", multi=True)
        target[3].click()
        # Selects hourly data for the past day
        target = self.get_el(By.CSS_SELECTOR, ".mat-option", multi=True)[-1]
        target.click()
        self.wait.until(lambda _: xhr_url in self.get_xhrs())
        usage = []
        while days > 0:
            xhrs = self.get_xhrs()
            details = json.loads(xhrs[xhr_url])
            # {
            #   "readDate":"2023-11-22",
            #   "readTime":"01:00",
            #   "usage":"1.682"
            # },
            for d in details.get("getIntervalUsageForDateResponseBody", {}).get("response", {}).get("intervalDataResponse", []):
                end_time = datetime.fromisoformat(f"{d['readDate']}T{d['readTime'].replace('24', '00')}:00")
                start_time = end_time - timedelta(hours=1)
                usage.append({
                    "startTime": start_time,
                    "endTime": end_time,
                    "usage": float(d.get("usage", 0)),
                    "amount": None,
                })
            days -= 1
            if days > 0:
                self.xhrs = {}
                target = self.get_el(By.CSS_SELECTOR, "button.link", text="PREVIOUS")
                try:
                    target.click()
                except ElementClickInterceptedException:
                    break
                try:
                    self.wait.until(lambda _: xhr_url in self.get_xhrs())
                except TimeoutException:
                    break
        return usage

    def download_daily_usage(self):
        self.goto_energy_usage()
        target = self.get_el(By.CSS_SELECTOR, "div.mat-form-field-infix", multi=True)
        target[3].click()
        target = self.get_el(By.CSS_SELECTOR, ".mat-option", multi=True)[-1]
        target.click()
        target = self.get_el(By.LINK_TEXT, "DOWNLOAD GREEN BUTTON DATA")
        target.click()
        self.wait.until(lambda d: len(d.get_downloadable_files()) == 1)
        files = self.br.get_downloadable_files()
        downloadable_file = files[0]
        target_directory = "/tmp"
        self.br.download_file(downloadable_file, target_directory)
        target_file = os.path.join(target_directory, downloadable_file)
        with open(target_file, "r") as file:
            return file.read()


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""


class AggregateType(Enum):
    """How to aggregate historical data."""

    MONTH = "month"
    DAY = "day"
    HOUR = "hour"

    def __str__(self) -> str:
        """Return the value of the enum."""
        return self.value


@dataclasses.dataclass
class Customer:
    """Data about a customer."""

    uuid: str


@dataclasses.dataclass
class Account:
    """Data about an account."""

    customer: Customer
    uuid: str
    utility_account_id: str


@dataclasses.dataclass
class Forecast:
    """Forecast data for an account."""

    account: Account
    start_date: date
    end_date: date
    current_date: date
    forecasted_cost: float
    forecasted_cost_low: float
    forecasted_cost_high: float


@dataclasses.dataclass
class CostRead:
    """A read from the meter that has both consumption and cost data."""

    start_time: datetime
    end_time: datetime
    consumption: float  # taken from value field, in KWH
    provided_cost: float  # in $


@dataclasses.dataclass
class UsageRead:
    """A read from the meter that has consumption data."""

    start_time: datetime
    end_time: datetime
    consumption: float  # taken from consumption.value field, in KWH


class RockyMountainPower:
    """Class that can get historical and forecasted usage/cost from Rocky Mountain Power."""

    def __init__(
        self,
        username: str,
        password: str,
        selenium_host: str = "localhost",
    ) -> None:
        """Initialize."""
        self.username: str = username
        self.password: str = password
        self.account = {}
        self.customer_id = None
        self.utility: RockyMountainPowerUtility = RockyMountainPowerUtility(selenium_host)

    def login(self) -> None:
        """Login to the utility website for access.

        :raises InvalidAuth: if login information is incorrect
        :raises CannotConnect: if we receive any HTTP error
        """
        self.utility.login(
            self.username, self.password
        )
        if not self.account:
            self.account = self.utility.account
        if not self.customer_id:
            self.customer_id = self.utility.user_id

    def end_session(self) -> None:
        self.utility.on_quit()

    def get_account(self) -> Account:
        """Get the account for the signed in user."""
        account = self._get_account()
        return Account(
            customer=Customer(uuid=self.customer_id),
            uuid=account["accountNumber"],
            utility_account_id=account["accountNumber"].replace(" ", "_"),
        )

    def get_forecast(self) -> list[Forecast]:
        """Get current and forecasted usage and cost for the current monthly bill.

        One forecast for each account, typically one for electricity.
        """
        forecasts = []
        self.utility.get_forecast()
        if self.utility.forecast:
            forecast = self.utility.forecast
            forecasts.append(
                Forecast(
                    account=Account(
                        customer=Customer(uuid=self.customer_id),
                        uuid=self.account["accountNumber"],
                        utility_account_id=self.account["accountNumber"],
                    ),
                    start_date=date.fromisoformat(forecast["startDateForAMIAcctView"]),
                    end_date=date.fromisoformat(forecast["endDateForAMIAcctView"]),
                    current_date=date.today(),
                    forecasted_cost=float(forecast.get("projectedCost", 0)),
                    forecasted_cost_low=float(forecast.get("projectedCostLow", 0)),
                    forecasted_cost_high=float(forecast.get("projectedCostHigh", 0)),
                )
            )
        return forecasts

    def _get_account(self) -> Any:
        """Get account associated with the user."""
        # Cache the account
        if not self.account:
            self.login()
            self.account = self.utility.account
        assert self.account
        return self.account

    def get_cost_reads(
        self,
        aggregate_type: AggregateType,
        period: Optional[int] = 1,
    ) -> list[CostRead]:
        """Get usage and cost data for the selected account in the given date range aggregated by month/day/hour.

        The resolution is typically hour, day, or month.
        Rocky Mountain Power typically keeps historical cost data for 2 years.
        """
        reads = self._get_dated_data(aggregate_type, period=period)
        result = []
        for read in reads:
            result.append(
                CostRead(
                    start_time=read["startTime"],
                    end_time=read["endTime"],
                    consumption=read["usage"],
                    provided_cost=read["amount"] or 0,
                )
            )
        # Remove last entries with 0 values
        while result:
            last = result.pop()
            if last.provided_cost != 0 or last.consumption != 0:
                result.append(last)
                break
        return result

    def _get_dated_data(
        self,
        aggregate_type: AggregateType,
        period: Optional[int] = 1,
    ) -> list[Any]:
        if aggregate_type == AggregateType.MONTH:
            return self.utility.get_usage_by_month()
        elif aggregate_type == AggregateType.DAY:
            return self.utility.get_usage_by_day(months=period)
        elif aggregate_type == AggregateType.HOUR:
            return self.utility.get_usage_by_hour(days=period)
        else:
            raise ValueError(f"aggregate_type {aggregate_type} is not valid")
