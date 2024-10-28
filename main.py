import os
import discord
from dotenv import load_dotenv
from openai import OpenAI
from lib.config import config
from tinydb import TinyDB, Query
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime

load_dotenv()

scheduled_messages_db = TinyDB('scheduled_messages.json')
users_info_db = TinyDB(
    'users_info.json')  # array of objects with user_id, info
scheduler = AsyncIOScheduler()
scheduler.configure(timezone="America/New_York")


class ScheduledMessage:

	def __init__(self, user_id, channel_id, message, schedule_type,
	             schedule_value):
		self.data = {
		    'user_id': user_id,
		    'channel_id': channel_id,
		    'message': message,
		    'schedule_type': schedule_type,  # 'daily', 'weekly', 'interval'
		    'schedule_value': schedule_value,  # time or cron expression
		    'created_at': datetime.now().isoformat()
		}


client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
bot = discord.Bot()


async def get_messages(channel: discord.TextChannel):
	messages = []
	async for message in channel.history(limit=None):
		messages.append({
		    "role": "user" if message.author == bot.user else "assistant",
		    "content": message.content
		})
	messages = messages[::-1]
	return messages


async def send_scheduled_message(channel_id, user_id, message):
	user = await bot.fetch_user(user_id)
	user_info = users_info_db.search(Query().user_id == user_id)

	# Generate message using LLM if user info exists
	if user_info:
		system_prompt = f"""You are a personal assistant helping to motivate and encourage the user.
Using the following information about the user, generate a personalized morning message:

User Information:
{user_info[0]['info']}

The message should:
1. Be encouraging and motivational
2. Reference specific goals or interests from their information
3. Include a practical suggestion for the day
4. Keep a friendly, supportive tone

Original message template: {message}"""

		messages = [{
		    "role": "system",
		    "content": system_prompt
		}, {
		    "role": "user",
		    "content": "Generate a personalized morning message."
		}]

		generated_message = get_chat_completion(messages)
		await user.send(generated_message)
	else:
		# Fall back to original message if no user info
		await user.send(message)


def get_chat_completion(messages):
	model = config.get('default_chat_model', 'gpt-4o-mini')
	response = client.chat.completions.create(
	    model=model,
	    messages=messages,
	)
	return response.choices[0].message.content


def schedule_job(schedule):
	if schedule['schedule_type'] == 'daily':
		scheduler.add_job(send_scheduled_message,
		                  'cron',
		                  hour=int(schedule['schedule_value'].split(':')[0]),
		                  minute=int(schedule['schedule_value'].split(':')[1]),
		                  args=[
		                      schedule['channel_id'], schedule['user_id'],
		                      schedule['message']
		                  ])
	elif schedule['schedule_type'] == 'weekly':
		# e.g. 'mon-13:00'
		day, time = schedule['schedule_value'].split('-')
		scheduler.add_job(send_scheduled_message,
		                  'cron',
		                  day_of_week=day,
		                  hour=int(time.split(':')[0]),
		                  minute=int(time.split(':')[1]),
		                  args=[
		                      schedule['channel_id'], schedule['user_id'],
		                      schedule['message']
		                  ])
	elif schedule['schedule_type'] == 'interval':
		# e.g. '1d' for every day, '1h' for every hour
		interval = schedule['schedule_value']
		scheduler.add_job(
		    send_scheduled_message,
		    'interval',
		    minutes=int(interval[:-1]) if interval.endswith('m') else 0,
		    hours=int(interval[:-1]) if interval.endswith('h') else 0,
		    days=int(interval[:-1]) if interval.endswith('d') else 0,
		    args=[
		        schedule['channel_id'], schedule['user_id'], schedule['message']
		    ])


@bot.command(name='chat', description='Start a chat with the AI assistant')
async def start_chat(
    ctx: discord.ApplicationContext,
    topic: discord.Option(
        str, description='The topic to start the chat with') = None):
	await bot.wait_until_ready()
	channel = ctx.channel
	title_prompt = f"""The following is a chat between a discord bot and a user. The user wants to talk about {topic}. Assistant's task is to write a title for the chat thread. Respond with the title and nothing else."""
	messages = [{"role": "system", "content": title_prompt}]
	title_response = get_chat_completion(messages)
	thread = await channel.create_thread(name=title_response)

	prompt = f"""The following is a chat between a discord bot and a user named {ctx.author.name}. The user started a chat with the bot and would like to talk about:\n{topic}.\n\nWrite an inviting message to start the conversation."""
	messages = [{"role": "system", "content": prompt}]
	response = get_chat_completion(messages)
	await thread.send(response)

	# todo make a natural message for this too
	await ctx.respond(f"Chat thread created: {thread.mention}")


@bot.command(name='set_info', description='Set your personal information')
async def set_info(ctx: discord.ApplicationContext, text: str):
	User = Query()
	users_info_db.upsert({
	    'user_id': ctx.author.id,
	    'info': text
	}, User.user_id == ctx.author.id)
	await ctx.respond("Information saved successfully!")


# set info from file
@bot.command(name='set_info_file',
             description='Set your personal information from a file')
async def set_info_file(ctx: discord.ApplicationContext, file: str):
	User = Query()
	with open(file, 'r') as f:
		text = f.read()
	users_info_db.upsert({
	    'user_id': ctx.author.id,
	    'info': text
	}, User.user_id == ctx.author.id)
	await ctx.respond("Information saved successfully!")


@bot.command(name='test', description='Do whatever the test function does')
async def test(ctx: discord.ApplicationContext):
	# send the schedule message to the user
	await ctx.respond("Test function called")

	await send_scheduled_message(ctx.channel.id, ctx.author.id, "Test message")


# save_chat - shoves history into json and sends it to the user
@bot.command(name='save_chat', description='Save the chat history')
async def save_chat(ctx: discord.ApplicationContext):
	messages = await get_messages(ctx.channel)
	with open('chat_history.json', 'w') as f:
		f.write(str(messages))
	# send the file to the user
	await ctx.respond(file=discord.File('chat_history.json'))


@bot.command(name='schedule')
async def schedule_message(ctx: discord.ApplicationContext, message: str,
                           schedule_type: str, schedule_value: str):
	scheduled_msg = ScheduledMessage(ctx.author.id, ctx.channel.id, message,
	                                 schedule_type, schedule_value)

	scheduled_messages_db.insert(scheduled_msg.data)
	schedule_job(scheduled_msg.data)

	await ctx.respond("Message scheduled successfully!")


@bot.command(name='list_schedules')
async def list_schedules(ctx: discord.ApplicationContext):
	User = Query()
	schedules = scheduled_messages_db.search(User.user_id == ctx.author.id)

	if not schedules:
		await ctx.respond("You have no scheduled messages.")
		return

	response = "Your scheduled messages:\n"
	for i, schedule in enumerate(schedules, 1):
		response += f"{i}. {schedule['message']} ({schedule['schedule_type']}: {schedule['schedule_value']})\n"

	await ctx.respond(response)


@bot.command(name='clear_schedules')
async def clear_schedules(ctx: discord.ApplicationContext):
	scheduled_messages_db.truncate()
	scheduler.remove_all_jobs()
	await ctx.respond("All scheduled messages cleared!")


@bot.command(name='clear_dms')
async def clear_dms(ctx: discord.ApplicationContext):
	await ctx.respond("Deleting my messages to you...")
	async for message in ctx.channel.history(limit=None):
		if message.author == bot.user:
			await message.delete()


@bot.event
async def on_message(message: discord.Message):
	if message.author.bot:
		return

	# if not isinstance(
	#     message.channel,
	#     discord.Thread) and message.channel.type != discord.ChannelType.private:
	# 	return
	if not isinstance(message.channel, discord.Thread):
		return

	# respond in threads

	threadId = message.channel.id
	print(f"Received message in thread {threadId}")
	thread = bot.get_channel(threadId)
	assert thread is not None
	assert isinstance(thread, discord.Thread)

	system_prompt = f"""The following is a chat between a discord bot and a user named {message.author.name}."""

	msgs = thread.history()
	chat_history = [{"role": "system", "content": system_prompt}]
	async for msg in msgs:
		role = "user" if msg.author == message.author else "assistant"
		chat_history.append({"role": role, "content": msg.content})
	chat_history = chat_history[::-1]

	response = get_chat_completion(chat_history)

	# discord's limit is 2000 characters
	# so break the response into chunks
	# and make a reply chain for the response
	last_message = None
	while response:
		if len(response) > 2000:
			# find the last newline character before 2000 to break the response
			# at a natural point and add ... to beginning of next message
			# to indicate that the message is continued
			response_chunk = response[:2000]
			newline_index = response_chunk.rfind('\n')
			if newline_index == -1:
				newline_index = 2000
			response = '...' + response[newline_index:]
		else:
			response_chunk = response
			response = None

		# use .reply instead of .send to make a reply chain
		if last_message:
			last_message = await last_message.reply(response_chunk)
		else:
			last_message = await message.channel.send(response_chunk)


def text_to_chunks(text, chunk_size=2000):
	chunks = []
	while text:
		if len(text) > chunk_size:
			chunk = text[:chunk_size]
			newline_index = chunk.rfind('\n')
			if newline_index == -1:
				newline_index = chunk_size
			chunks.append(text[:newline_index])
			text = text[newline_index:].lstrip('\n')
		else:
			chunks.append(text)
			text = None
	return chunks


@bot.event
async def on_ready():
	assert bot.user is not None
	print(f'Logged in as {bot.user.name}')

	schedules = scheduled_messages_db.all()
	for schedule in schedules:
		schedule_job(schedule)

	scheduler.start()


bot.run(os.getenv('DISCORD_TOKEN'))
