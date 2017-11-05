"""
Support for Coinbase wallets. The component will create one sensor
for each wallet and one for your fiat account.

https://github.com/coinbase/coinbase-python
https://developers.coinbase.com/api/v2
"""
from datetime import timedelta
import logging
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (CONF_NAME, ATTR_ATTRIBUTION)
from homeassistant.helpers.entity import Entity
from homeassistant.util import Throttle

REQUIREMENTS = ['coinbase==2.0.6']

_LOGGER = logging.getLogger(__name__)

CONF_API_KEY = 'api_key'
CONF_API_SECRET = 'api_secret'
CONF_NATIVE_BALANCE = 'native_balance'
CONF_EXCLUDE = 'exclude_wallet'
CONF_ATTRIBUTION = "Data provided by Coinbase.com"

# Return cached results if last scan was less then this time ago
MIN_TIME_BETWEEN_SCANS = timedelta(seconds=60)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_API_KEY): cv.string,
    vol.Required(CONF_API_SECRET): cv.string,
    vol.Optional(CONF_NATIVE_BALANCE, default=False): cv.boolean,
    vol.Optional(CONF_EXCLUDE, default=None): cv.ensure_list,
})


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Setup the sensor platform."""
    from coinbase.wallet.client import Client as coinb
    from coinbase.wallet.error import AuthenticationError
    client = coinb(config.get(CONF_API_KEY), config.get(CONF_API_SECRET))
    try:
        accounts = client.get_accounts()
    except AuthenticationError as e:
        _LOGGER.error("Sensor setup: Coinbase Auth error ({})".format(e))
        return False
    _LOGGER.debug("Sensor setup: Connecion to Coinbase established.")
    sensors = []
    for account in accounts.data:
        if account.name not in config.get(CONF_EXCLUDE):
            name = "Coinbase " + account.name
            sensors.append(CoinbaseSensor(
                config, name, account.id, config.get(CONF_NATIVE_BALANCE))
            )
            _LOGGER.debug(
                "Sensor setup: Added sensor for account '{}'.".format(
                    account.name
                )
            )
        else:
            _LOGGER.debug("Sensor setup: Excluded account '{}'.".format(
                account.name))

    add_devices(sensors)
    _LOGGER.debug("Sensor setup: Complete.")


class CoinbaseSensor(Entity):
    """Representation of a Sensor."""

    def __init__(self, config, name, account_id, native):
        """Initialize the sensor."""
        self._name = name
        self.account_id = account_id
        self.native = native
        self.data = CoinbaseClient(config, account_id)
        self.data.update()
        self._unit_of_measurement = None
        self._state = self.data.balance(native)

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        if self.native is True:
            return self.data.native_balance_currency
        return self.data.balance_currency

    @property
    def state_attributes(self):
        """Return the device state attributes."""
        attrs = {
            # 'id': self.data.account_id, # Disabled for securety
            'resource': self.data.resource,
            'primary': self.data.primary,
            'type': self.data.account_type,
            'created_at': self.data.created_at,
            'updated_at': self.data.updated_at,
            'balance_amount': self.data.balance_amount,
            'balance_currency': self.data.balance_currency,
            'native_balance_amount': self.data.native_balance_amount,
            'native_balance_currency': self.data.native_balance_currency,
            'show_native': self.native,
        }
        attrs_wallet = {
            'buy_price': self.data.buy_price,
            'sell_price': self.data.sell_price,
            'spot_price': self.data.spot_price,
            'exch_rate_native_usd': self.data.exch_rate_native_usd,
        }
        if self.data.account_type == 'wallet':
            attrs.update(attrs_wallet)
        return attrs

    def update(self):
        """Fetch new state data for the sensor."""
        self.data.update()
        self._state = self.data.balance(self.native)


class CoinbaseClient(object):
    """Get the latest data from Coinbase."""

    def __init__(self, config, account_id):
        """Initialize the sensor."""
        self.haconf = config
        self.account_id = account_id
        self.account_type = None
        self.created_at = None
        self.updated_at = None
        self.primary = None
        self.resource = None
        self.balance_amount = None
        self.balance_currency = None
        self.native_balance_amount = None
        self.native_balance_currency = None
        self.buy_price = None
        self.sell_price = None
        self.spot_price = None
        self.exch_rate_native_usd = None

    def balance(self, native):
        if native is True:
            return self.native_balance_amount
        return self.balance_amount

    @staticmethod
    def coinbasehttp(query, currency, currency_native):
        import urllib.request
        from urllib.error import HTTPError
        import json
        base_url = 'https://api.coinbase.com/v2/'
        api_version = '2017-10-26'
        queries = {
            'buy_price': 'prices/{}-{}/buy'.format(
                currency, currency_native),
            'sell_price': 'prices/{}-{}/sell'.format(
                currency, currency_native),
            'spot_price': 'prices/{}-{}/spot'.format(
                currency, currency_native),
            'exchange_rate': 'exchange-rates',
        }
        req_url = base_url + queries.get(query)
        req = urllib.request.Request(req_url)
        req.add_header('CB-VERSION', api_version)

        try:
            r = urllib.request.urlopen(req)
        except HTTPError as e:
            error_msg = "Coinbase API error ({}): {}".format(req_url, e)
            _LOGGER.error(error_msg)
            return error_msg
        else:
            data = json.loads(r.read())['data']
            if query == 'exchange_rate':
                rates = data['rates']
                if currency_native in rates:
                    return rates[currency_native]
                else:
                    return 'Currency "{}" not supported by Coinbase'.format(
                        currency_native)
            else:
                return data['amount']

    @Throttle(MIN_TIME_BETWEEN_SCANS)
    def update(self):
        """Get the latest data from Coinbase."""
        from coinbase.wallet.client import Client as coinb
        from coinbase.wallet.error import AuthenticationError
        client = coinb(self.haconf.get(CONF_API_KEY),
                       self.haconf.get(CONF_API_SECRET))
        try:
            account = client.get_account(self.account_id)
        except AuthenticationError as e:
            _LOGGER.error("Update sensor: Auth error ({}).".format(e))
            return False
        self.created_at = account.created_at
        self.primary = account.primary
        self.account_type = account.type
        self.resource = account.resource
        self.balance_amount = account.balance.amount
        self.balance_currency = account.balance.currency
        self.native_balance_amount = account.native_balance.amount
        self.native_balance_currency = account.native_balance.currency
        self.updated_at = account.updated_at
        self.exch_rate_native_usd = self.coinbasehttp(
            'exchange_rate',
            None,
            account.native_balance.currency
        )
        if self.account_type == 'wallet':
            self.buy_price = self.coinbasehttp(
                'buy_price',
                account.balance.currency,
                account.native_balance.currency)
            self.sell_price = self.coinbasehttp(
                'sell_price',
                account.balance.currency,
                account.native_balance.currency
            )
            self.spot_price = self.coinbasehttp(
                'spot_price',
                account.balance.currency,
                account.native_balance.currency
            )
