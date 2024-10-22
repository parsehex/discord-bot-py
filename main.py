import os
import discord
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
bot = discord.Bot()


def get_chat_completion(messages):
	response = client.chat.completions.create(
	    model="gpt-4o-mini",
	    messages=messages,
	)
	return response.choices[0].message.content


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
	# thread = await ctx.message.create_thread(name=title_response)

	prompt = f"""The following is a chat between a discord bot and a user named {ctx.author.name}. The user started a chat with the bot and would like to talk about:\n{topic}.\n\nWrite an inviting message to start the conversation."""
	messages = [{"role": "system", "content": prompt}]
	response = get_chat_completion(messages)
	await thread.send(response)

	# todo make a natural message for this too
	await ctx.respond(f"Chat thread created: {thread.mention}")


@bot.event
async def on_message(message):
	if message.author.bot:
		return

	print('got user message')

	if not isinstance(message.channel, discord.Thread):
		return

	threadId = message.channel.id
	thread = bot.get_channel(threadId)
	assert thread is not None
	assert isinstance(thread, discord.Thread)

	msgs = thread.history()
	chat_history = []
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


@bot.event
async def on_ready():
	assert bot.user is not None
	print(f'Logged in as {bot.user.name}')


bot.run(os.getenv('DISCORD_TOKEN'))
