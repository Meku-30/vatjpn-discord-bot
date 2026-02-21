import discord
import asyncio
import requests
import json
import configparser
import re
import traceback
import os

# load config
config = configparser.ConfigParser()
config.read("settings.ini")

vatsim_stat_json_url = config["VATSIM_CONFIG"]["vatsim_stat_json_url"]
vatsim_stat_retrieve_period = float(config["VATSIM_CONFIG"]["vatsim_stat_retrieve_period"])
vatsim_controller_callsign_filter_regex = config["VATSIM_CONFIG"]["vatsim_controller_callsign_filter_regex"]
pattern = re.compile(vatsim_controller_callsign_filter_regex)

discord_bot_client_token = os.environ.get("DISCORD_BOT_TOKEN") or config["DISCORD_CONFIG"]["discord_bot_client_token"]
discord_channel_id = int(config["DISCORD_CONFIG"]["discord_channel_id"])

data_filename = config["DATAFILE_CONFIG"]["data_filename"]




rating_list = ["Unknown", "OBS", "S1", "S2", "S3", "C1", "C2", "C3", "I1", "I2", "I3", "SUP", "ADM"]

intents = discord.Intents.default()
client = discord.Client(intents=intents)

def get_old():
    try:
        with open(data_filename, "r") as old_file:
            return json.loads(old_file.read())
    except:
        return {}

def get_new():
    vatsim_info = requests.get(vatsim_stat_json_url).json()
    controllers = vatsim_info["controllers"]
    controllers_map = { controllers[i]["callsign"]: controllers[i] for i in range(0, len(controllers)) }

    # print(vatsim_info["general"])

    return controllers_map

def get_controllers():
    old_stat = get_old()
    new_stat = get_new()

    # save current
    with open(data_filename, "w") as a_file:
        json.dump(new_stat, a_file)

    connected_controllers = { k : new_stat[k] for k in set(new_stat) - set(old_stat) }
    disconnected_controllers = { k : old_stat[k] for k in set(old_stat) - set(new_stat) }

    # filter
    connected_controllers = { d: connected_controllers[d] for d in connected_controllers if pattern.match(connected_controllers[d]['callsign']) is not None and connected_controllers[d]["rating"]>1 }
    disconnected_controllers = { d: disconnected_controllers[d] for d in disconnected_controllers if pattern.match(disconnected_controllers[d]['callsign']) is not None and disconnected_controllers[d]["rating"]>1 }
    all_controllers = { d: new_stat[d] for d in new_stat if pattern.match(new_stat[d]['callsign']) is not None and new_stat[d]["rating"]>1 }


    return all_controllers, connected_controllers, disconnected_controllers



def get_discord_embed(connect_type, atc_info, current_list):

    if connect_type == "connect":
        current_list = [ "{}{}".format(current_list[d]["callsign"], "("+current_list[d]["frequency"]+")" if current_list[d]["frequency"] != "199.998" else "") for d in current_list ]
        embed = discord.Embed( title = atc_info['callsign'] + ' - ' + connect_type, color = 0x00ff00, description = '< online list >\n' + '\n'.join(current_list))
        embed.add_field(name = 'Rating', value = rating_list[atc_info["rating"]])
        embed.add_field(name = 'CID', value = atc_info["cid"])
        embed.add_field(name = 'Server', value = atc_info["server"])
        return embed

    if connect_type == "disconnect":
        current_list = [ "{}{}".format(current_list[d]["callsign"], "("+current_list[d]["frequency"]+")" if current_list[d]["frequency"] != "199.998" else "") for d in current_list ]
        embed = discord.Embed( title = atc_info['callsign'] + ' - ' + connect_type, color = 0xff0000, description = '< online list >\n' + '\n'.join(current_list))
        embed.add_field(name = 'Rating', value = rating_list[atc_info["rating"]])
        embed.add_field(name = 'CID', value = atc_info["cid"])
        embed.add_field(name = 'Server', value = atc_info["server"])
        return embed


async def run():
    await client.wait_until_ready()

    while not client.is_closed():
        try:
            all_controllers, connected_controllers, disconnected_controllers = get_controllers()

            channel = client.get_channel(discord_channel_id)

            for a in connected_controllers:
                await channel.send(embed = get_discord_embed('connect', connected_controllers[a], all_controllers))
            for a in disconnected_controllers:
                await channel.send(embed = get_discord_embed('disconnect', disconnected_controllers[a], all_controllers))

        except Exception as e:
                traceback.print_exc()

        finally:
            await asyncio.sleep(vatsim_stat_retrieve_period)

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    # 二重起動防止：すでにループが走っていないか確認
    if not hasattr(client, 'loop_started'):
        client.loop.create_task(run())
        client.loop_started = True

client.run(discord_bot_client_token)
