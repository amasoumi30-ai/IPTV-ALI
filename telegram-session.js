const { TelegramClient } = require('telegram');
const { StringSession } = require('telegram/sessions');
const readline = require('readline');

const apiId = Number(process.env.TELEGRAM_API_ID);
const apiHash = process.env.TELEGRAM_API_HASH;

if (!apiId || !apiHash) {
  console.error('Missing TELEGRAM_API_ID or TELEGRAM_API_HASH');
  process.exit(1);
}

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
const ask = (q) => new Promise(resolve => rl.question(q, resolve));

(async () => {
  const client = new TelegramClient(new StringSession(''), apiId, apiHash, { connectionRetries: 5 });
  await client.start({
    phoneNumber: async () => await ask('Telegram phone number (international format): '),
    phoneCode: async () => await ask('Telegram login code: '),
    password: async () => await ask('2FA password (if enabled): '),
    onError: (err) => console.error(err.message)
  });
  console.log('\nLOGIN_OK');
  console.log('TELEGRAM_SESSION=' + client.session.save());
  await client.disconnect();
  rl.close();
})().catch(err => {
  console.error(err);
  rl.close();
  process.exit(1);
});
