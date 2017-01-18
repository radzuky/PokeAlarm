#!/usr/bin/python
# -*- coding: utf-8 -*-

# Monkey Patch to allow Gevent's Concurrency
from gevent import monkey
monkey.patch_all()

# Setup Logging
import logging
logging.basicConfig(format='%(asctime)s [%(processName)15.15s][%(name)10.10s][%(levelname)8.8s] %(message)s',
                    level=logging.INFO)


# Standard Library Imports
import configargparse
from gevent import wsgi, spawn
import Queue
import json
import os
import sys
# 3rd Party Imports
from flask import Flask, request
# Local Imports
from PokeAlarm import config
from PokeAlarm.Manager import Manager
from PokeAlarm.Structures import PokemonGoMap
from PokeAlarm.Utils import get_path, parse_unicode

# Reinforce UTF-8 as default
reload(sys)
sys.setdefaultencoding('UTF8')

# Set up logging

log = logging.getLogger('Server')

# Global Variables
app = Flask(__name__)
data_queue = Queue.Queue()
managers = {}


@app.route('/', methods=['GET'])
def index():
    return "PokeAlarm Running!"


@app.route('/', methods=['POST'])
def accept_webhook():
    log.debug("POST request received from {}.".format(request.remote_addr))
    data = json.loads(request.data)
    data_queue.put(data)
    return "OK"  # request ok


# Thread used to distribute the data into various processes (for PokemonGo-Map format)
def manage_webhook_data(queue):
    while True:
        if queue.qsize() > 300:
            log.warning("Queue length is at {}... this may be causing a delay in notifications.".format(queue.qsize()))
        data = queue.get(block=True)
        obj = PokemonGoMap.make_object(data)
        if obj is not None:
            for name, mgr in managers.iteritems():
                mgr.update(obj)
                log.debug("Distributed to {}.".format(name))
            log.debug("Finished distributing object with id {}".format(obj['id']))
        queue.task_done()


# Configure and run PokeAlarm
def start_server():
    log.setLevel(logging.INFO)
    logging.getLogger('PokeAlarm').setLevel(logging.INFO)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('pyswgi').setLevel(logging.WARNING)
    logging.getLogger('connectionpool').setLevel(logging.WARNING)
    logging.getLogger('gipc').setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    parse_settings(os.path.abspath(os.path.dirname(__file__)))

    # Start Webhook Manager in a Thread
    spawn(manage_webhook_data, data_queue)

    # Start up Server
    log.info("Webhook server running on http://%s:%s" % (config['HOST'], config['PORT']))
    server = wsgi.WSGIServer((config['HOST'], config['PORT']), app, log=logging.getLogger('pyswgi'))
    server.serve_forever()


################################################## CONFIG UTILITIES  ###################################################


def parse_settings(root_path):
    config['ROOT_PATH'] = root_path
    parser = configargparse.ArgParser(default_config_files=[get_path('config/config.ini')])
    parser.add_argument('-d', '--debug', help='Debug Mode', action='store_true', default=False)
    parser.add_argument('-H', '--host', help='Set web server listening host', default='127.0.0.1')
    parser.add_argument('-P', '--port', type=int, help='Set web server listening port', default=4000)
    parser.add_argument('-m', '--mgr_count', type=int, default=1,
                        help='Number of Manager processes to start.')
    parser.add_argument('-M', '--managers', type=parse_unicode, action=AppendPlus, default=[],
                        help='Names of Manager processes to start.')
    parser.add_argument('-k', '--key', type=parse_unicode, action=AppendPlus, default=[None],
                        help='Specify a Google API Key to use.')
    parser.add_argument('-f', '--filters', type=parse_unicode, action=AppendPlus, default=['filters.json'],
                        help='Filters configuration file. default: filters.json', )
    parser.add_argument('-a', '--alarms', type=parse_unicode, action=AppendPlus, default=['alarms.json'],
                        help='Alarms configuration file. default: alarms.json', )
    parser.add_argument('-gf', '--geofences', type=parse_unicode, action=AppendPlus, default=[None],
                        help='Alarms configuration file. default: None')
    parser.add_argument('-l', '--location', action=AppendPlus, default=[None],
                        help='Location, can be an address or coordinates')
    parser.add_argument('-L', '--locale', type=parse_unicode, action=AppendPlus, default=['en'],
                        choices=['de', 'en', 'fr', 'it', 'pt_br', 'ru', 'zh_cn', 'zh_hk', 'zh_tw'],
                        help='Locale for Pokemon and Move names: default en, check locale folder for more options')
    parser.add_argument('-u', '--units', type=parse_unicode, default=['imperial'], action=AppendPlus,
                        choices=['metric', 'imperial'],
                        help='Specify either metric or imperial units to use for distance measurements. ')
    parser.add_argument('-tl', '--timelimit', type=int, default=[0], action=AppendPlus,
                        help='Minimum number of seconds remaining on a pokemon to send a notify')
    parser.add_argument('-tz', '--timezone', type=parse_unicode, action=AppendPlus, default=[None],
                        help='Timezone used for notifications.  Ex: "America/Los_Angeles"')

    args = parser.parse_args()

    if args.debug:
        log.setLevel(logging.DEBUG)
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger('PokeAlarm').setLevel(logging.DEBUG)
        logging.getLogger('Manager').setLevel(logging.DEBUG)
        log.debug("Debug mode enabled!")

    config['HOST'] = args.host
    config['PORT'] = args.port
    config['QUIET'] = False
    config['DEBUG'] = args.debug

    for list_ in [args.key, args.filters, args.alarms, args.geofences, args.location, args.units, args.timelimit]:
        log.debug(list_)
        size = len(list_)
        if size != 1 and size != args.mgr_count:
            log.critical("\n !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n" +
                         "Incorrect number of arguments applied: must be either 1 for all processes or else the " +
                         "number of arguments must match the number of processes. Process will exit.")
            log.critical(list_)
            sys.exit(1)

    # Construct the managers
    for m_ct in range(args.mgr_count):
        m = Manager(
            name=args.managers[m_ct] if m_ct < len(args.managers) else "Manager_{}".format(m_ct),
            google_key=args.key[m_ct] if len(args.key) > 1 else args.key[0],
            filters=args.filters[m_ct] if len(args.filters) > 1 else args.filters[0],
            geofences=args.geofences[m_ct] if len(args.geofences) > 1 else args.geofences[0],
            alarms=args.alarms[m_ct] if len(args.alarms) > 1 else args.alarms[0],
            location=args.location[m_ct] if len(args.location) > 1 else args.location[0],
            locale=args.locale[m_ct] if len(args.locale) > 1 else args.locale[0],
            units=args.units[m_ct] if len(args.units) > 1 else args.units[0],
            time_limit=args.timelimit[m_ct] if len(args.timelimit) > 1 else args.timelimit[0],
            timezone=args.timezone[m_ct] if len(args.timezone) > 1 else args.timezone[0]
        )
        if m.get_name() not in managers:
            # Add the manager to the map
            managers[m.get_name()] = m
        else:
            log.critical("\n\n\n !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n" +
                         "Names of Manager processes must be unique (regardless of capitalization)! Process will exit.")
            sys.exit(1)


# Class uses to help replace defaults with new arguements
class AppendPlus(configargparse.Action):
    def __call__(self, parser, namespace, values, option_strings=None):
        dest = getattr(namespace, self.dest, None)
        if not hasattr(dest, 'extend') or dest == self.default:
            dest = []
            setattr(namespace, self.dest, dest)
            parser.set_defaults(**{self.dest: None})

            dest.append(values)

########################################################################################################################


if __name__ == '__main__':
    start_server()