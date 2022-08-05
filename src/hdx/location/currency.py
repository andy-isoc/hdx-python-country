"""Currency conversion"""
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Union

from hdx.utilities.dateparse import get_timestamp_from_datetime, parse_date
from hdx.utilities.dictandlist import dict_of_dicts_add
from hdx.utilities.downloader import Download, DownloadError
from hdx.utilities.path import get_temp_dir
from hdx.utilities.retriever import Retrieve

logger = logging.getLogger(__name__)


class CurrencyError(Exception):
    pass


class Currency:
    """Currency class for performing currency conversion. Uses Yahoo, falling back on
    exchangerate.host for current rates and Yahoo falling back on IMF for historic
    rates. Note that rate calls are cached.
    """

    _primary_rates_url = "https://query2.finance.yahoo.com/v8/finance/chart/{currency}=X?period1={date}&period2={date}&interval=1d&events=div%2Csplit&formatted=false&lang=en-US&region=US&corsDomain=finance.yahoo.com"
    _secondary_rates_url = "https://api.exchangerate.host/latest?base=usd"
    _secondary_historic_url = (
        "https://codeforiati.org/imf-exchangerates/imf_exchangerates.csv"
    )
    _cached_current_rates = None
    _cached_historic_rates = None
    _rates_api = None
    _secondary_rates = None
    _secondary_historic = None
    _fallback_to_current = False
    _no_historic = False
    _user_agent = "hdx-python-country-rates"
    _retriever = None

    @classmethod
    def _get_int_timestamp(cls, date: datetime) -> int:
        return int(
            round(
                get_timestamp_from_datetime(date.replace(tzinfo=timezone.utc))
            )
        )

    @classmethod
    def setup(
        cls,
        retriever: Optional[Retrieve] = None,
        primary_rates_url: str = _primary_rates_url,
        secondary_rates_url: str = _secondary_rates_url,
        secondary_historic_url: str = _secondary_historic_url,
        fallback_historic_to_current: bool = False,
        fallback_current_to_static: bool = False,
        no_historic: bool = False,
    ) -> None:
        """
        Setup the sources. If you wish to use a static fallback file by setting
        fallback_current_to_static to True, it needs to be named "secondary_rates.json"
        and put in the fallback_dir of the passed in Retriever.

        Args:
            retriever (Optional[Retrieve]): Retrieve object to use for downloading. Defaults to None (generate a new one).
            primary_rates_url (str): Primary rates url to use. Defaults to Yahoo API.
            secondary_rates_url (str): Current rates url to use. Defaults to exchangerate.host.
            secondary_historic_url (str): Historic rates url to use. Defaults to IMF (via IATI).
            fallback_historic_to_current (bool): If historic unavailable, fallback to current. Defaults to False.
            fallback_current_to_static (bool): Use static file as final fallback. Defaults to False.
            no_historic (bool): Do not set up historic rates. Defaults to False.

        Returns:
            None
        """

        cls._cached_current_rates = {"USD": 1}
        cls._cached_historic_rates = dict()
        cls._rates_api = primary_rates_url
        cls._secondary_rates = None
        cls._secondary_historic = None
        if retriever is None:
            downloader = Download(user_agent=cls._user_agent)
            temp_dir = get_temp_dir(cls._user_agent)
            retriever = Retrieve(
                downloader,
                None,
                temp_dir,
                temp_dir,
                save=False,
                use_saved=False,
            )
        cls._retriever = retriever
        try:
            secondary_rates = retriever.download_json(
                secondary_rates_url,
                "secondary_rates.json",
                "secondary current exchange rates",
                fallback_current_to_static,
            )
            cls._secondary_rates = secondary_rates["rates"]
        except (DownloadError, OSError):
            logger.exception("Error getting secondary current rates!")
            cls._secondary_rates = "FAIL"
        if no_historic:
            cls._no_historic = True
        if cls._no_historic:
            return
        try:
            _, iterator = retriever.get_tabular_rows(
                secondary_historic_url,
                dict_form=True,
                filename="historic_rates.csv",
                logstr="secondary historic exchange rates",
            )
            cls._secondary_historic = dict()
            for row in iterator:
                currency = row["Currency"]
                date = cls._get_int_timestamp(parse_date(row["Date"]))
                rate = float(row["Rate"])
                dict_of_dicts_add(
                    cls._secondary_historic, currency, date, rate
                )
        except (DownloadError, OSError):
            logger.exception("Error getting secondary historic rates!")
            cls._secondary_historic = "FAIL"
        cls._fallback_to_current = fallback_historic_to_current

    @classmethod
    def _get_primary_rates_data(
        cls, currency: str, timestamp: int, downloader=None
    ) -> Optional[Dict]:
        """
        Get the primary fx rate data for currency

        Args:
            currency (str): Currency
            timestamp (int): Timestamp to use for fx conversion

        Returns:
            Optional[float]: fx rate or None
        """
        if cls._rates_api is None:
            Currency.setup()
        url = cls._rates_api.format(currency=currency, date=str(timestamp))
        if downloader is None:
            downloader = cls._retriever
        try:
            chart = downloader.download_json(url)["chart"]
            if chart["error"] is not None:
                return None
            return chart["result"][0]
        except (DownloadError, KeyError):
            return None

    @classmethod
    def _get_primary_current_rate(cls, currency: str) -> Optional[float]:
        """
        Get the primary current fx rate for currency

        Args:
            currency (str): Currency

        Returns:
            Optional[float]: fx rate or None
        """
        data = cls._get_primary_rates_data(
            currency, cls._get_int_timestamp(datetime.now())
        )
        if not data:
            return None
        return data["meta"]["regularMarketPrice"]

    @classmethod
    def _get_secondary_current_rate(cls, currency: str) -> Optional[float]:
        """
        Get the secondary current fx rate for currency

        Args:
            currency (str): Currency

        Returns:
            Optional[float]: fx rate or None
        """
        if cls._secondary_rates is None:
            Currency.setup()
        if cls._secondary_rates == "FAIL":
            return None
        return cls._secondary_rates.get(currency)

    @classmethod
    def get_current_rate(cls, currency: str) -> float:
        """
        Get the current fx rate for currency

        Args:
            currency (str): Currency

        Returns:
            float: fx rate
        """
        currency = currency.upper()
        if cls._cached_current_rates is None:
            Currency.setup()
        fx_rate = cls._cached_current_rates.get(currency)
        if fx_rate is not None:
            return fx_rate
        fx_rate = cls._get_primary_current_rate(currency)
        if fx_rate is not None:
            cls._cached_current_rates[currency] = fx_rate
            return fx_rate
        fx_rate = cls._get_secondary_current_rate(currency)
        if fx_rate is not None:
            logger.warning(f"Using secondary current rate for {currency}!")
            cls._cached_current_rates[currency] = fx_rate
            return fx_rate
        raise CurrencyError(f"Failed to get rate for currency {currency}!")

    @classmethod
    def get_current_value_in_usd(
        cls, value: Union[int, float], currency: str
    ) -> float:
        """
        Get the current USD value of the value in local currency

        Args:
            value (Union[int, float]): Value in local currency
            currency (str): Currency

        Returns:
            float: Value in USD
        """
        currency = currency.upper()
        if currency == "USD":
            return value
        fx_rate = cls.get_current_rate(currency)
        return value / fx_rate

    @classmethod
    def get_current_value_in_currency(
        cls, usdvalue: Union[int, float], currency: str
    ) -> float:
        """
        Get the current value in local currency of the value in USD

        Args:
            usdvalue (Union[int, float]): Value in USD
            currency (str): Currency

        Returns:
            float: Value in local currency
        """
        currency = currency.upper()
        if currency == "USD":
            return usdvalue
        fx_rate = cls.get_current_rate(currency)
        return usdvalue * fx_rate

    @classmethod
    def _get_primary_historic_rate(
        cls, currency: str, timestamp: int
    ) -> Optional[float]:
        """
        Get the primary fx rate for currency on a particular date

        Args:
            currency (str): Currency
            timestamp (int): Timestamp to use for fx conversion

        Returns:
            Optional[float]: fx rate or None
        """
        data = cls._get_primary_rates_data(currency, timestamp)
        if not data:
            return None
        logger.info(data)
        return data["indicators"]["adjclose"][0]["adjclose"][0]

    @classmethod
    def _get_interpolated_rate(
        cls, timestamp1, rate1, timestamp2, rate2, desired_date
    ):
        return rate1 + (desired_date - timestamp1) * (
            (rate2 - rate1) / (timestamp2 - timestamp1)
        )

    @classmethod
    def _get_secondary_historic_rate(
        cls, currency: str, timestamp: int
    ) -> Optional[float]:
        """
        Get the secondary fx rate for currency on a particular date

        Args:
            currency (str): Currency
            timestamp (int): Timestamp to use for fx conversion

        Returns:
            Optional[float]: fx rate or None
        """
        if cls._secondary_historic is None:
            Currency.setup()
        if cls._secondary_historic == "FAIL":
            return None
        currency_data = cls._secondary_historic.get(currency)
        if currency_data is None:
            return None
        fx_rate = currency_data.get(timestamp)
        if fx_rate:
            return fx_rate
        timestamp1 = None
        timestamp2 = None
        for ts in currency_data.keys():
            if timestamp > ts:
                timestamp1 = ts
            else:
                timestamp2 = ts
                break
        if timestamp1 is None:
            if timestamp2 is None:
                return None
            return currency_data[timestamp2]
        if timestamp2 is None:
            return currency_data[timestamp1]
        return cls._get_interpolated_rate(
            timestamp1,
            currency_data[timestamp1],
            timestamp2,
            currency_data[timestamp2],
            timestamp,
        )

    @classmethod
    def get_historic_rate(cls, currency: str, date: datetime) -> float:
        """
        Get the fx rate for currency on a particular date

        Args:
            currency (str): Currency
            date (datetime): Date to use for fx conversion

        Returns:
            float: fx rate
        """
        currency = currency.upper()
        if currency == "USD":
            return 1
        if cls._cached_historic_rates is None:
            Currency.setup()
        currency_data = cls._cached_historic_rates.get(currency)
        timestamp = cls._get_int_timestamp(date)
        if currency_data is not None:
            fx_rate = currency_data.get(timestamp)
            if fx_rate is not None:
                return fx_rate
        fx_rate = cls._get_primary_historic_rate(currency, timestamp)
        if fx_rate is not None:
            dict_of_dicts_add(
                cls._cached_historic_rates, currency, timestamp, fx_rate
            )
            return fx_rate
        fx_rate = cls._get_secondary_historic_rate(currency, timestamp)
        if fx_rate is not None:
            dict_of_dicts_add(
                cls._cached_historic_rates, currency, timestamp, fx_rate
            )
            return fx_rate
        if cls._fallback_to_current:
            fx_rate = cls.get_current_rate(currency)
            if fx_rate:
                logger.warning(
                    "Falling back to current rate for currency {currency} on date {date}!"
                )
            return fx_rate
        raise CurrencyError(
            f"Failed to get rate for currency {currency} on date {date}!"
        )

    @classmethod
    def get_historic_value_in_usd(
        cls, value: Union[int, float], currency: str, date: datetime
    ) -> float:
        """
        Get the USD value of the value in local currency on a particular date

        Args:
            value (Union[int, float]): Value in local currency
            currency (str): Currency
            date (datetime): Date to use for fx conversion

        Returns:
            float: Value in USD
        """
        currency = currency.upper()
        if currency == "USD":
            return value
        fx_rate = cls.get_historic_rate(currency, date)
        return value / fx_rate

    @classmethod
    def get_historic_value_in_currency(
        cls, usdvalue: Union[int, float], currency: str, date: datetime
    ) -> float:
        """
        Get the current value in local currency of the value in USD on a particular date

        Args:
            value (Union[int, float]): Value in USD
            currency (str): Currency
            date (datetime): Date to use for fx conversion

        Returns:
            float: Value in local currency
        """
        currency = currency.upper()
        if currency == "USD":
            return usdvalue
        fx_rate = cls.get_historic_rate(currency, date)
        return usdvalue * fx_rate
