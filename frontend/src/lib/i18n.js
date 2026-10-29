/** Client-side RU/EN catalogue.
 *
 * Deliberately a flat object rather than an i18n library: the TMA ships a few
 * dozen strings and must stay small and instant. Keys mirror the backend's
 * locale files where the two overlap, so a string only has one spelling.
 *
 * A missing key returns the key itself — louder than silently falling back to
 * the other language, which is how half-translated UIs ship.
 */

export const LANGUAGES = ['ru', 'en'];

const catalogues = {
  ru: {
    'app.title': 'Домены',
    'app.subtitle': 'Регистрация за 5 минут',

    'nav.search': 'Поиск',
    'nav.domains': 'Мои домены',
    'nav.orders': 'Заказы',

    'search.placeholder': 'Введите имя домена',
    'search.action': 'Найти',
    'search.hint': 'Например: mycompany',
    'search.empty': 'Ничего не найдено.',
    'search.invalid': 'Некорректное имя домена.',
    'search.available': 'Свободен',
    'search.taken': 'Занят',
    'search.premium': 'Премиум',
    'search.zones': 'Зоны',
    'search.all_zones': 'Все зоны',
    'search.start': 'Начните с имени домена',
    'search.start_hint': 'Проверим все зоны сразу и покажем цены в рублях.',

    'buy.title': 'Оформление',
    'buy.years': 'Срок',
    'buy.years_value': '{years} г.',
    'buy.total': 'Итого',
    'buy.pay': 'Оплатить {price} ₽',
    'buy.owner': 'Данные владельца',
    'buy.owner_saved': 'Используем сохранённые данные',
    'buy.owner_change': 'Изменить',
    'buy.creating': 'Создаём заказ…',
    'buy.taken_now': 'Этот домен только что заняли. Попробуйте другое имя.',
    'buy.registrar_down': 'Регистратор недоступен. Попробуйте через минуту.',

    'contact.first_name': 'Имя',
    'contact.last_name': 'Фамилия',
    'contact.email': 'Email',
    'contact.phone_cc': 'Код страны',
    'contact.phone': 'Телефон',
    'contact.address': 'Адрес',
    'contact.city': 'Город',
    'contact.zip': 'Индекс',
    'contact.country': 'Страна (2 буквы)',
    'contact.save': 'Сохранить и продолжить',
    'contact.hint': 'Нужны для регистрации домена. Сохраним для будущих покупок.',
    'contact.required': 'Заполните обязательные поля.',

    'pay.waiting': 'Ожидаем оплату…',
    'pay.open': 'Открыть оплату',
    'pay.check': 'Я оплатил',
    'pay.registering': 'Оплата получена. Регистрируем домен…',
    'pay.done': 'Готово! Домен ваш.',
    'pay.refund': 'Домен не удалось получить. Оформляем возврат.',
    'pay.failed': 'Оплата не прошла.',

    'domains.empty': 'У вас пока нет доменов.',
    'domains.expires': 'Истекает {date}',
    'domains.ns': 'NS-серверы',
    'domains.ns_edit': 'Изменить NS',
    'domains.ns_save': 'Сохранить',
    'domains.ns_saved': 'NS-серверы обновлены.',
    'domains.ns_hint': 'По одному серверу в строке. Например, Cloudflare.',
    'domains.ns_empty': 'Укажите хотя бы один сервер.',
    'domains.ns_failed': 'Не удалось обновить NS. Попробуйте позже.',
    'domains.manage': 'Управление',

    'orders.empty': 'Заказов пока нет.',
    'orders.retry_pay': 'Оплатить',

    'status.draft': 'Черновик',
    'status.awaiting_payment': 'Ожидает оплаты',
    'status.paid': 'Оплачен',
    'status.registering': 'Регистрируется',
    'status.completed': 'Готов',
    'status.failed': 'Ошибка',
    'status.refund_required': 'Возврат средств',
    'status.refunded': 'Возвращён',
    'status.cancelled': 'Отменён',

    'common.loading': 'Загрузка…',
    'common.retry': 'Повторить',
    'common.error': 'Что-то пошло не так.',
    'common.back': 'Назад',
    'common.cancel': 'Отмена',
    'common.close': 'Закрыть',
    'common.from': 'от',
    'common.network': 'Нет соединения с сервером.',
    'common.no_user': 'Откройте приложение через Telegram-бота.',
  },
  en: {
    'app.title': 'Domains',
    'app.subtitle': 'Registered in 5 minutes',

    'nav.search': 'Search',
    'nav.domains': 'My domains',
    'nav.orders': 'Orders',

    'search.placeholder': 'Enter a domain name',
    'search.action': 'Search',
    'search.hint': 'For example: mycompany',
    'search.empty': 'Nothing found.',
    'search.invalid': 'Invalid domain name.',
    'search.available': 'Available',
    'search.taken': 'Taken',
    'search.premium': 'Premium',
    'search.zones': 'Zones',
    'search.all_zones': 'All zones',
    'search.start': 'Start with a domain name',
    'search.start_hint': 'We check every zone at once and show prices in RUB.',

    'buy.title': 'Checkout',
    'buy.years': 'Term',
    'buy.years_value': '{years} yr',
    'buy.total': 'Total',
    'buy.pay': 'Pay {price} ₽',
    'buy.owner': 'Owner details',
    'buy.owner_saved': 'Using your saved details',
    'buy.owner_change': 'Change',
    'buy.creating': 'Creating order…',
    'buy.taken_now': 'That domain was just taken. Try another name.',
    'buy.registrar_down': 'The registrar is unavailable. Try again in a minute.',

    'contact.first_name': 'First name',
    'contact.last_name': 'Last name',
    'contact.email': 'Email',
    'contact.phone_cc': 'Country code',
    'contact.phone': 'Phone',
    'contact.address': 'Address',
    'contact.city': 'City',
    'contact.zip': 'ZIP',
    'contact.country': 'Country (2 letters)',
    'contact.save': 'Save and continue',
    'contact.hint': 'Required to register the domain. We save them for next time.',
    'contact.required': 'Please fill in the required fields.',

    'pay.waiting': 'Waiting for payment…',
    'pay.open': 'Open payment',
    'pay.check': "I've paid",
    'pay.registering': 'Payment received. Registering the domain…',
    'pay.done': 'Done! The domain is yours.',
    'pay.refund': 'We could not get the domain. A refund is being issued.',
    'pay.failed': 'Payment failed.',

    'domains.empty': 'You have no domains yet.',
    'domains.expires': 'Expires {date}',
    'domains.ns': 'Nameservers',
    'domains.ns_edit': 'Edit NS',
    'domains.ns_save': 'Save',
    'domains.ns_saved': 'Nameservers updated.',
    'domains.ns_hint': 'One server per line. Cloudflare, for example.',
    'domains.ns_empty': 'Enter at least one server.',
    'domains.ns_failed': 'Could not update nameservers. Try again later.',
    'domains.manage': 'Manage',

    'orders.empty': 'No orders yet.',
    'orders.retry_pay': 'Pay',

    'status.draft': 'Draft',
    'status.awaiting_payment': 'Awaiting payment',
    'status.paid': 'Paid',
    'status.registering': 'Registering',
    'status.completed': 'Completed',
    'status.failed': 'Failed',
    'status.refund_required': 'Refund pending',
    'status.refunded': 'Refunded',
    'status.cancelled': 'Cancelled',

    'common.loading': 'Loading…',
    'common.retry': 'Retry',
    'common.error': 'Something went wrong.',
    'common.back': 'Back',
    'common.cancel': 'Cancel',
    'common.close': 'Close',
    'common.from': 'from',
    'common.network': 'No connection to the server.',
    'common.no_user': 'Open this app from the Telegram bot.',
  },
};

export function normalizeLanguage(language) {
  return LANGUAGES.includes(language) ? language : 'ru';
}

/** Look up `key` and interpolate `{placeholders}`. */
export function translate(key, language = 'ru', vars = {}) {
  const catalogue = catalogues[normalizeLanguage(language)];
  const template = catalogue[key];
  if (template === undefined) return key;
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match,
  );
}

export function makeTranslator(language) {
  const fn = (key, vars) => translate(key, language, vars);
  fn.language = normalizeLanguage(language);
  return fn;
}

/** Prices are shown without kopecks: domain prices are round and the extra
 *  digits only add noise on a narrow screen. */
export function formatPrice(value, language = 'ru') {
  const number = Number(value || 0);
  const locale = language === 'en' ? 'en-US' : 'ru-RU';
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(number);
}

export function formatDate(value, language = 'ru') {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(language === 'en' ? 'en-GB' : 'ru-RU', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  }).format(date);
}

export default catalogues;
