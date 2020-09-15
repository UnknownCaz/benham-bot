import discord
import random
import os

commands = ["$roll","$stats","$help"]

client = discord.Client()
@client.event
async def on_ready():
    print('We have logged in as {0.user}'.format(client))

def stat_roll(loops ,times, sides):
    total = 0
    totals = []
    response = []
    for r in range(1,loops+1):
        numbers = []
        for i in range(1,times+1):
            number = random.randint(1,sides)
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

def roll(count, times,):
    total = 0
    response = ""
    count = int(command[1])
    sides = int(command[2])
    for x in range(1,count+1):
        number = random.randint(1,sides)
        print(number)
        numbers.append(str(number))
        total += number
    response = "You rolled {0}, the sum is {1}".format(numbers,total)
@client.event
async def on_message(message):
    #print(message.content)
    if message.author == client.user:
        return
    else:
        if message.content.lower().replace("d"," ").split(" ")[0] == commands[0]:
                command = message.content.lower().replace("d"," ").split(" ")
                numbers = []
            await message.channel.send()
        if(message.content.startswith(commands[1])):
            command = message.content.split(" ")
            d1 = int(command[1])
            print(d1)
            d2 = int(command[2])
            print(d2)
            d3 = int(command[3])
            await message.channel.send(roll_roll(d1,d2,d3))
        if(message.content.startswith(commands[2])):
            response = ''
            for i in commands:
                response = response + " " + str(i)
            await message.channel.send(response)

    

client.run(os.environ['BOT_KEY'])

