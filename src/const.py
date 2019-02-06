from .extra import utils

WORK_DIR = utils.get_proj_dir('output')
LOG_DIR = utils.get_proj_dir('logs')
OVERALL_MIN_DAILY_VOLUME = 10
PAIR_MIN_DAILY_VOLUME = 5
VOLS_LIMITS = {'1.3.0': 15, '1.3.113': 15, '1.3.1570': 15, '1.3.121': 15}
NODE_URI = 'ws://130.193.42.72:8090/ws'
WALLET_URI = 'ws://127.0.0.1:8093/ws'
DATA_UPDATE_TIME = 3                                                    # hours
