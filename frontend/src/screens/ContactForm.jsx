/** Contact (WHOIS owner) form.
 *
 * Shown exactly once per customer. The fields are the registrar's minimum —
 * anything more would break the "no paperwork" promise — and the profile is
 * saved server-side so the next purchase skips this screen entirely.
 */
import { useState } from 'react';

import { Spinner } from '../components/ui';

const REQUIRED = [
  'first_name',
  'last_name',
  'email',
  'phone_country_code',
  'phone_number',
  'address_line1',
  'city',
  'zip_code',
  'country',
];

const EMPTY = {
  first_name: '',
  last_name: '',
  email: '',
  phone_country_code: '7',
  phone_number: '',
  address_line1: '',
  city: '',
  zip_code: '',
  country: 'RU',
};

export function validateContact(values) {
  const missing = REQUIRED.filter((field) => !String(values[field] ?? '').trim());
  if (missing.length) return { ok: false, missing };
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(values.email)) {
    return { ok: false, missing: ['email'] };
  }
  if (String(values.country).trim().length !== 2) {
    return { ok: false, missing: ['country'] };
  }
  return { ok: true, missing: [] };
}

export default function ContactForm({ t, onSubmit, onCancel, submitting = false }) {
  const [values, setValues] = useState(EMPTY);
  const [touched, setTouched] = useState(false);

  const validation = validateContact(values);

  function field(name, label, props = {}) {
    const invalid = touched && validation.missing.includes(name);
    return (
      <label className="block">
        <span className="mb-1.5 block text-xs font-medium text-muted">{label}</span>
        <input
          className={`input ${invalid ? 'border-danger/60' : ''}`}
          value={values[name]}
          onChange={(event) =>
            setValues((current) => ({ ...current, [name]: event.target.value }))
          }
          aria-label={label}
          aria-invalid={invalid || undefined}
          {...props}
        />
      </label>
    );
  }

  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        setTouched(true);
        if (!validation.ok) return;
        onSubmit({
          ...values,
          country: values.country.trim().toUpperCase(),
        });
      }}
    >
      <p className="text-sm text-muted">{t('contact.hint')}</p>

      <div className="grid grid-cols-2 gap-3">
        {field('first_name', t('contact.first_name'), { autoComplete: 'given-name' })}
        {field('last_name', t('contact.last_name'), { autoComplete: 'family-name' })}
      </div>

      {field('email', t('contact.email'), {
        type: 'email',
        inputMode: 'email',
        autoComplete: 'email',
      })}

      <div className="grid grid-cols-[6rem_1fr] gap-3">
        {field('phone_country_code', t('contact.phone_cc'), { inputMode: 'tel' })}
        {field('phone_number', t('contact.phone'), {
          inputMode: 'tel',
          autoComplete: 'tel',
        })}
      </div>

      {field('address_line1', t('contact.address'), { autoComplete: 'address-line1' })}

      <div className="grid grid-cols-2 gap-3">
        {field('city', t('contact.city'), { autoComplete: 'address-level2' })}
        {field('zip_code', t('contact.zip'), { autoComplete: 'postal-code' })}
      </div>

      {field('country', t('contact.country'), { maxLength: 2 })}

      {touched && !validation.ok ? (
        <p role="alert" className="text-sm text-danger">
          {t('contact.required')}
        </p>
      ) : null}

      <div className="flex items-center gap-2 pt-1">
        {onCancel ? (
          <button
            type="button"
            className="btn-ghost flex-1 py-2.5 text-sm"
            onClick={onCancel}
            disabled={submitting}
          >
            {t('common.back')}
          </button>
        ) : null}
        <button
          type="submit"
          className="btn-primary flex-1 py-2.5 text-sm"
          disabled={submitting}
        >
          {submitting ? <Spinner /> : null}
          {t('contact.save')}
        </button>
      </div>
    </form>
  );
}
