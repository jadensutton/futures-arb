import ftx_lib
import json

from time import sleep
from datetime import datetime, timedelta

if __name__ == '__main__':
    config = json.loads(open('config.json').read())

    API_KEY = config['api_key']
    SECRET_KEY = config['secret_key']
    SUB_ACCOUNT_NAME = config['sub_account_name']

    client = ftx_lib.FtxClient(api_key=API_KEY, api_secret=SECRET_KEY, subaccount_name=SUB_ACCOUNT_NAME)

    print('[+] Delivery-Perpetual Futures Arbitrage [+]\n')
    exp_date = input('Future expiry date (MMDD) \n>>>')
    leverage = int(input('Leverage (x) \n>>>'))

    blacklist = ['USDT/USD']
    spot_coins = [x['baseCurrency'] for x in client.get_markets() if x['type'] == 'spot' and 'tokenizedEquity' not in x.keys() and x['name'] not in blacklist]
    perp_futures = [x['name'][:-5] for x in client.get_markets() if x['type'] == 'future' and x['name'][-4:] == 'PERP' and x['name'][:-5] in spot_coins]
    available_coins = [x['name'][:-5] for x in client.get_markets() if x['type'] == 'future' and x['name'][-4:] == exp_date and x['name'][:-5] in spot_coins and x['name'][:-5] in perp_futures]

    today = datetime.today()
    year = today.year
    exp_datetime = datetime.strptime('{month}/{day}/{year}'.format(month=exp_date[:2], day=exp_date[2:], year=year), '%m/%d/%Y')
    time_to_expiry = (exp_datetime - datetime.now()).total_seconds() - (86400 * 7)

    max_position_size = leverage * [x['total'] for x in client.get_balances() if x['coin'] == 'USD'][0]

    arb_yields = {}
    for coin in available_coins:
        spot = '{}/USD'.format(coin)
        del_future = '{coin}-{exp}'.format(coin=coin, exp=exp_date)
        perp_future = '{}-PERP'.format(coin)

        spot_price = client.get_market(spot)['last']
        del_futures_price = client.get_market(del_future)['last']
        perp_futures_price = client.get_market(del_future)['last']

        del_mispricing = del_futures_price / spot_price - 1
        perp_funding_rate = -1 * client.get_future_stats(perp_future)['nextFundingRate']

        yields = {}
        del_asks = client.get_orderbook(del_future)['asks']
        perp_asks = client.get_orderbook(perp_future)['asks']
        for size in range(100, max_position_size, 100):
            del_orderbook_size = sum([ask * qty for ask, qty in del_asks])
            perp_orderbook_size = sum([ask * qty for ask, qty in perp_asks])
            max_size = min(del_orderbook_size, perp_orderbook_size, size / 2)

            del_size = max_size
            del_fills = []
            for ask, qty in del_asks:
                current_size = sum([a * q for a, q in del_fills])
                if current_size + ask * qty >= del_size:
                    del_fills.append([ask, (del_size - current_size) / ask])
                    break
                else:
                    del_fills.append([ask, qty])

            total_qty = sum([x[1] for x in del_fills])
            del_fill_price = sum([ask * qty / total_qty for ask, qty in del_fills])

            perp_size = max_size
            perp_fills = []
            for ask, qty in perp_asks:
                current_size = sum([a * q for a, q in perp_fills])
                if current_size + ask * qty >= perp_size:
                    perp_fills.append([ask, (perp_size - current_size) / ask])
                    break
                else:
                    perp_fills.append([ask, qty])

            total_qty = sum([x[1] for x in perp_fills])
            perp_fill_price = sum([ask * qty / total_qty for ask, qty in perp_fills])

            yields[size] = size * (0.5 * (0.9993 * (perp_funding_rate * (time_to_expiry / 3600)) - 1.0007 * (del_fill_price / spot_price - 1)) - abs(del_fill_price - del_futures_price) / del_futures_price - abs(perp_fill_price - perp_futures_price) / perp_futures_price)

        optimal_size = sorted(yields, key=yields.get)[-1]
        arb_yields[coin] = {'size': optimal_size, 'profit': yields[optimal_size], 'return': yields[optimal_size] / optimal_size}

    top_coins = sorted(arb_yields, key=lambda x: arb_yields[x]['return'])[-3:]
    exit_queue = []
    remaining_funds = max_position_size
    for coin in top_coins:
        position_size = min(remaining_funds, arb_yields[coin]['size'])
        if position_size >= 10:
            remaining_funds -= position_size

            perpetual = '{}-PERP'.format(coin)
            delivery = '{coin}-{exp}'.format(coin=coin, exp=exp_date)

            perpetual_precision = client.get_market(perpetual)['sizeIncrement']
            delivery_precision = client.get_market(delivery)['sizeIncrement']

            del_asks = client.get_orderbook(delivery)['asks']
            del_size = position_size / 2
            del_fills = []
            for ask, qty in del_asks:
                current_size = sum([a * q for a, q in del_fills])
                if current_size + ask * qty >= del_size:
                    del_fills.append([ask, (del_size - current_size) / ask])
                    break
                else:
                    del_fills.append([ask, qty])

            total_qty = sum([x[1] for x in del_fills])
            del_fill_price = sum([ask * qty / total_qty for ask, qty in del_fills])

            perp_asks = client.get_orderbook(perpetual)['asks']
            perp_size = position_size / 2
            perp_fills = []
            for ask, qty in perp_asks:
                current_size = sum([a * q for a, q in perp_fills])
                if current_size + ask * qty >= perp_size:
                    perp_fills.append([ask, (perp_size - current_size) / ask])
                    break
                else:
                    perp_fills.append([ask, qty])

            total_qty = sum([x[1] for x in perp_fills])
            perp_fill_price = sum([ask * qty / total_qty for ask, qty in perp_fills])

            perpetual_qty = ((perp_size / perp_fill_price) // perpetual_precision / (1 / perpetual_precision))
            delivery_qty = ((del_size / del_fill_price) // delivery_precision / (1 / delivery_precision))

            client.place_order(perpetual, 'buy', 0, perpetual_qty, 'market')
            client.place_order(delivery, 'sell', 0, delivery_qty, 'market')

            exit_queue.append([perpetual, 'sell', 0, perpetual_qty, 'market'])
            exit_queue.append([delivery, 'buy', 0, delivery_qty, 'market'])

            print('LONG {perpetual}, SHORT {delivery}'.format(perpetual=perpetual, delivery=delivery))
            print('Theoretical Profit: {profit}'.format(profit=arb_yields[coin]['return']))

    sleep(time_to_expiry)

    for ticker, side, limit, qty, type in exit_queue:
        client.place_order(ticker, side, limit, qty, type)
