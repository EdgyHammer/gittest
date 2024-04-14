import interactions
from interactions import Client, Intents
from interactions import Message
from interactions import Extension, BaseContext, listen
from interactions import ActionRow, Button, ButtonStyle
from interactions import Modal, ShortText, ModalContext
from interactions import SlashCommand, SlashContext
from interactions.api.events import Ready, Component, ThreadCreate, MessageReactionAdd
from interactions.models.discord.channel import GuildForum, GuildForumPost
from enum import IntEnum
from typing import List, Dict
import json
import aiofiles
import asyncio
import datetime
import os
import re

user_balance_database_file_path = 'Data/user_balance.json'
COMPETITION_GUILD_ID: int = 1200434448425033788
COMPETITION_FORUM_CHANNEL_ID: int = 1228907795563151511
BOT_USERNAME = 'OGAS'
ARTICLE_VALIDITY_THRESHOLD: int = 500
ARTICLE_AUTHOR_REWARD: float = 300


class CompetitionPhase(IntEnum):
    PREMATCH = 1
    ONGOING = 2
    GRADING = 3
    CONCLUDING = 4


# A participant class to store and manage user data in situ.
class Participant:
    def __init__(self, username: str):
        self.is_article_author: bool = False
        self.already_UBIed: bool = False
        self.balance: float = 0
        self.username: str = username
        self.bet_choices: dict = {}

    def bet(self, thread_id: int, amount: float):
        self.bet_choices[thread_id] = amount
        self.balance -= amount

    def collect_reward(self, winner_thread_id: int, odds: float):
        if winner_thread_id in self.bet_choices:
            self.balance += self.bet_choices[winner_thread_id] * odds

    async def collect_ubi(self, event: Component):
        if not self.already_UBIed:
            self.balance += 100
            self.already_UBIed = True
            await event.ctx.send('成功领取100枚代币', delete_after=10, ephemeral=True)
        else:
            await event.ctx.send('不能重复领取代币，您可以发表文章获取额外300代币。', delete_after=10, ephemeral=True)

    def __str__(self):
        temp_dict = {
            'user id': self.username,
            'is content creator': self.is_article_author,
            'already UBIed': self.already_UBIed,
            'balance': self.balance,
            'bet on': self.bet_choices
        }
        temp_str = f"{temp_dict}"
        return temp_str

    def __eq__(self, other):
        try:
            if self.username == other.username:
                return True
        except TypeError:
            print('User type mismatch')


# # A control panel class to manage the control panel thread.
class ControlPanel:
    def __init__(self, channel: GuildForum):
        self.thread_title: str = '投注站'
        self.thread_content: str = '比赛投稿阶段，任何成员均可从此处领取总计100枚竞猜代币，此代币可以用于押注本次比赛的优胜文章。'
        self.start_date: str = datetime.datetime.today().strftime("%Y/%m/%d")
        self.channel: GuildForum = channel
        self.thread: GuildForumPost = None
        self.all_participants: List[Participant] = []
        self.all_articles_thread_id: List[int] = []
        self.all_bets_vs_thread_id: Dict[int, float] = {}
        self.all_odds_vs_thread_id: Dict[int, float] = {}
        self.phase = CompetitionPhase.PREMATCH

        self.main_menu_ui: list[ActionRow] = [
            ActionRow(
                Button(
                    custom_id='test' + self.start_date,
                    style=ButtonStyle.GREEN,
                    label='测试'
                ),
                Button(
                    custom_id='collect_ubi',
                    style=ButtonStyle.GREEN,
                    label='领取代币'
                ),
                Button(
                    custom_id='set_phase:' + 'ongoing',
                    style=ButtonStyle.GREEN,
                    label='开始比赛'
                ),
                Button(
                    custom_id='set_phase:' + 'grading',
                    style=ButtonStyle.RED,
                    label='开放投票'
                ),
                Button(
                    custom_id='set_phase:' + 'concluding',
                    style=ButtonStyle.BLURPLE,
                    label='公布结果'
                )
            )
        ]

    # On competition setup complete and turned into pre-match phase, bot create a control panel thread, where admin can manage the competition
    # and members can bet.
    async def create_control_panel_thread(self):
        post: GuildForumPost = await self.channel.create_post(name=self.thread_title, content=self.thread_content, components=self.main_menu_ui)
        self.thread = post

    def print_competition_info(self):
        print(self.thread, self.phase, self.channel, self.start_date, self.all_articles_thread_id, self.all_participants)
        for aParticipant in self.all_participants:
            print(aParticipant)

    async def add_new_bet_option_ui(self, article_thread: GuildForumPost):
        temp_thread_id: int = article_thread.id
        temp_initial_message = await article_thread.fetch_message(temp_thread_id)
        temp_article_author_id: str = str(temp_initial_message.author.username)
        temp_article_title: str = article_thread.name
        temp_url = f"https://discord.com/channels/{COMPETITION_FORUM_CHANNEL_ID}/{temp_thread_id}"
        item_ui = [
            ActionRow(
                Button(
                    custom_id='bet' + str(temp_thread_id),
                    style=ButtonStyle.BLUE,
                    label='押注此文'
                )
            )
        ]
        if len(temp_initial_message.content) > ARTICLE_VALIDITY_THRESHOLD:
            await self.thread.send(content=f'{temp_article_author_id}\n{temp_article_title}\n{temp_url}\n', components=item_ui)
            self.all_articles_thread_id.append(temp_thread_id)

    async def send_bet_modal(self, event: Component):
        ctx = event.ctx
        bet_modal = Modal(
            ShortText(label="押注金额", value="0", custom_id="amount_input"),
            title="确认押注金额",
        )
        await ctx.send_modal(modal=bet_modal)
        modal_ctx: ModalContext = await ctx.bot.wait_for_modal(bet_modal)

        amount = 0
        try:
            amount = int(modal_ctx.responses["amount_input"])
        except ValueError:
            await modal_ctx.send('请输入整数。', delete_after=5, ephemeral=True)
        temp_username = modal_ctx.author.username
        temp_participant = Participant(temp_username)

        if temp_participant not in self.all_participants:
            await modal_ctx.send('您还没有用于押注的代币，请用本帖的领取代币按钮领取100代币（仅限一次），或参与投稿获得300代币。', delete_after=5,
                                 ephemeral=True)
        else:
            for aParticipant in self.all_participants:
                if aParticipant == temp_participant:
                    if aParticipant.balance < amount:
                        await modal_ctx.send(f'您有{aParticipant.balance}个代币，请用本帖的领取代币按钮领取100代币（仅限一次），或参与投稿获得300代币。',
                                             delete_after=5,
                                             ephemeral=True)
                    else:
                        bet_on_thread_id = int(event.ctx.custom_id[3:])
                        temp_url = f"https://discord.com/channels/{COMPETITION_FORUM_CHANNEL_ID}/{bet_on_thread_id}"
                        await modal_ctx.send(f'您使用{amount}个代币押注了{temp_url}',
                                             delete_after=5,
                                             ephemeral=True)
                        aParticipant.bet(bet_on_thread_id, float(amount))

    async def send_announcement_modal(self, event: Component):
        ctx = event.ctx
        announcement_modal = Modal(
            ShortText(label="优胜文章id", value="1234567890123", custom_id="winner_thread_id"),
            title="确认押注金额",
        )
        await ctx.send_modal(modal=announcement_modal)
        modal_ctx: ModalContext = await ctx.bot.wait_for_modal(announcement_modal)

        try:
            temp_winner_thread_id = int(modal_ctx.responses["winner_thread_id"])
            self.calculate_odds()
            self.distribute_bet_rewards(temp_winner_thread_id)
        except ValueError:
            await modal_ctx.send('请输入整数。', delete_after=5, ephemeral=True)

    def calculate_odds(self):
        total_bet = 0
        for aParticipant in self.all_participants:
            for aThread in aParticipant.bet_choices:
                if aThread not in self.all_bets_vs_thread_id:
                    self.all_bets_vs_thread_id[aThread] = aParticipant.bet_choices[aThread]
                    total_bet += aParticipant.bet_choices[aThread]
                elif aThread in self.all_bets_vs_thread_id:
                    self.all_bets_vs_thread_id[aThread] += aParticipant.bet_choices[aThread]
                    total_bet += aParticipant.bet_choices[aThread]

        for aThread in self.all_bets_vs_thread_id:
            self.all_odds_vs_thread_id[aThread] = total_bet / self.all_bets_vs_thread_id[aThread]

    def distribute_bet_rewards(self, winner_article_thread_id: int):
        winner_odd = self.all_odds_vs_thread_id[winner_article_thread_id]
        for aParticipant in self.all_participants:
            aParticipant.collect_reward(winner_article_thread_id, winner_odd)


# A user database manager class, similar to the one used in stock manager.
async def write_json(all_participants: List[Participant]):
    temp_dict: dict = {}
    for aParticipant in all_participants:
        temp_dict[aParticipant.username] = aParticipant.balance

    async with aiofiles.open(user_balance_database_file_path, 'w', encoding='utf-8') as f:
        json_data = json.dumps(temp_dict, ensure_ascii=False, indent=4)
        await f.write(json_data)


# # A GUI thread for admins to manage the events, including closing the betting session, announcing the end of the event, the winner.
# # In side this same GUI thread, there should be also a CLI output of the current status of each competitor, i.e. the total bet amount,
# # the number of members betting for this competitor and the odds.

# # Buttons manager: add buttons to each thread of essay. Buttons including the bet for this thread button,
# # A button will provoke a bet modal, asking the participants for bet amount.

# Algorithms to actually make things happen.
# # A premature reactions remover
async def remove_premature_reactions(reaction_found_on_threadmessage: Message):
    await reaction_found_on_threadmessage.clear_all_reactions()


# # Grant reward to content creator
async def grant_reward_to_article_author(
        article_author: Participant,
        article_message: Message,
        existing_participants: List[Participant],
        threshold: int,
        amount: float
):
    if article_author not in existing_participants:

        if len(article_message.content) >= threshold:
            article_author.is_article_author = True
            article_author.balance += amount
            existing_participants.append(article_author)

    else:
        for aParticipant in existing_participants:
            if aParticipant == article_author and not aParticipant.is_article_author:
                if len(article_message.content) >= threshold:
                    aParticipant.is_article_author = True
                    aParticipant.balance += amount


# # An odds calculator

# The extension class that puts everything together.

class CompetitionExtension(Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot

        # self.COMPETITION_THREAD_ID: int = 1228196847668170812

        self.channel: GuildForum = None
        self.control_panel: ControlPanel = None

    module_base = SlashCommand(
        name="bet",
        description="Bet utilities for essay competition."
    )

    @module_base.subcommand(sub_cmd_name='test', sub_cmd_description='test command, for test only')
    async def test(self, ctx: SlashContext):
        self.control_panel.print_competition_info()

    @module_base.subcommand(sub_cmd_name='setup_competition', sub_cmd_description='Set up the competition bet environments.')
    async def setup_competition(self, ctx: SlashContext):
        self.channel = self.bot.get_channel(COMPETITION_FORUM_CHANNEL_ID)
        print(self.channel)
        self.control_panel = ControlPanel(self.channel)
        await self.control_panel.create_control_panel_thread()

    @listen(Component)
    async def on_any_button(self, event: Component):
        ctx = event.ctx
        print(ctx.custom_id)

        # When competition start, bot grant rewards to authors who's already written an article.
        if ctx.custom_id == 'set_phase:' + 'ongoing' and self.control_panel.phase != CompetitionPhase.ONGOING:
            print(f"Competition started.")
            self.control_panel.phase = CompetitionPhase.ONGOING
            all_existing_threads = await self.channel.fetch_posts()
            for aThread in all_existing_threads:
                temp_thread_id = aThread.id
                temp_thread_message = await aThread.fetch_message(temp_thread_id)
                temp_participant = Participant(str(temp_thread_message.author.username))
                await grant_reward_to_article_author(temp_participant, temp_thread_message, self.control_panel.all_participants,
                                                     ARTICLE_VALIDITY_THRESHOLD,
                                                     ARTICLE_AUTHOR_REWARD)
                await self.control_panel.add_new_bet_option_ui(aThread)

        elif ctx.custom_id == 'set_phase:' + 'grading':
            self.control_panel.phase = CompetitionPhase.GRADING

        elif ctx.custom_id == 'set_phase:' + 'concluding':
            self.control_panel.phase = CompetitionPhase.CONCLUDING
            await self.control_panel.send_announcement_modal(event)

            temp_competition_result = ''

            for aParticipant in self.control_panel.all_participants:
                temp_competition_result += str(aParticipant.balance) + '\n'

            print(temp_competition_result)

            await ctx.send(temp_competition_result)

        elif ctx.custom_id == 'collect_ubi':
            temp_participant = Participant(str(ctx.author.username))

            if temp_participant not in self.control_panel.all_participants:
                await temp_participant.collect_ubi(event)
                self.control_panel.all_participants.append(temp_participant)
            else:
                for aParticipant in self.control_panel.all_participants:
                    if aParticipant == temp_participant and not aParticipant.already_UBIed:
                        await aParticipant.collect_ubi(event)

        elif ctx.custom_id[:3] == 'bet':
            await self.control_panel.send_bet_modal(event)

    @listen(ThreadCreate)
    async def on_new_thread(self, event: ThreadCreate):
        if self.channel != event.thread.parent_channel:
            print('Thread filtered.')
        else:
            temp_thread_id = event.thread.id
            temp_thread_message = await event.thread.fetch_message(temp_thread_id)
            temp_username = str(temp_thread_message.author.username)
            temp_participant = Participant(temp_username)

            if self.control_panel.phase == CompetitionPhase.ONGOING:
                await self.control_panel.add_new_bet_option_ui(event.thread)
                await grant_reward_to_article_author(temp_participant, temp_thread_message, self.control_panel.all_participants,
                                                     ARTICLE_VALIDITY_THRESHOLD,
                                                     ARTICLE_AUTHOR_REWARD)

    @listen(MessageReactionAdd)
    async def on_reaction_added(self, event: MessageReactionAdd):
        temp_message = event.message
        temp_message_id = event.message.id
        if self.control_panel.phase == CompetitionPhase.ONGOING and temp_message_id in self.control_panel.all_articles_thread_id:
            await remove_premature_reactions(temp_message)
