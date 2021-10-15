import discord
import random
import os
import dotenv
from dotenv import load_dotenv

load_dotenv("environ.env")

# commands = ["$roll","$stats","$help"]
client = discord.Client()


@client.event
async def on_ready():
    print("We have logged in as {0.user}".format(client))


def stat_roll(loops, times, sides):
    total = 0
    totals = []
    response = []
    for r in range(1, loops + 1):
        numbers = []
        for i in range(1, times + 1):
            number = random.randint(1, sides)
            numbers.append(number)
        numbers = sorted(numbers)
        numbers.pop(0)
        for x in numbers:
            total += x
        totals.append(total)
        response.append(totals)
        total = 0
        totals = []
    print(response)
    return response


def roll_roll(dice, sides):
    total = 0
    numbers = []
    for i in range(1, int(dice) + 1):
        number = random.randint(1, int(sides))
        numbers.append(number)
        total += number
    return [numbers, total]


@client.event
async def on_message(message):
    # print(message.content)
    if message.author == client.user:
        return
    else:
        command = message.content.lower().replace("d", " ").split(" ")
        if message.content.lower().replace("d", " ").split(" ")[0] == "$roll":
            print(command)
            roll = roll_roll(command[1], command[2])
            await message.channel.send(
                "You rolled {0}, the sum is {1}".format(roll[0], roll[1])
            )
        if message.content.startswith("$stats"):
            com = message.content.split(" ")
            d1 = int(com[1])
            d2 = int(com[2])
            d3 = int(com[3])
            await message.channel.send(stat_roll(d1, d2, d3))
        if message.content.startwith("$print"):
            mess = message.content.split(" ")
            await message.channel.send(mess[1])


client.run(os.environ["BOT_KEY"])
