/**
 * Filter option constants for list pages
 * @module lib/constants/filters
 */

/** @type {{ value: string, label: string }[]} */
export const LEAD_STATUSES = [
  { value: 'ALL', label: 'All Statuses' },
  { value: 'ASSIGNED', label: 'Assigned' },
  { value: 'IN_PROCESS', label: 'In Process' },
  { value: 'CONVERTED', label: 'Converted' },
  { value: 'RECYCLED', label: 'Recycled' },
  { value: 'CLOSED', label: 'Closed' }
];

/** @type {{ value: string, label: string }[]} */
export const LEAD_SOURCES = [
  { value: 'ALL', label: 'All Sources' },
  { value: 'call', label: 'Call' },
  { value: 'email', label: 'Email' },
  { value: 'existing customer', label: 'Existing Customer' },
  { value: 'partner', label: 'Partner' },
  { value: 'public relations', label: 'Public Relations' },
  { value: 'compaign', label: 'Campaign' },
  { value: 'other', label: 'Other' }
];

/** @type {{ value: string, label: string }[]} */
export const LEAD_RATINGS = [
  { value: 'ALL', label: 'All Ratings' },
  { value: 'HOT', label: 'Hot' },
  { value: 'WARM', label: 'Warm' },
  { value: 'COLD', label: 'Cold' }
];

/** @type {{ value: string, label: string }[]} */
export const CASE_STATUSES = [
  { value: 'ALL', label: 'All Statuses' },
  { value: 'New', label: 'New' },
  { value: 'Assigned', label: 'Assigned' },
  { value: 'Pending', label: 'Pending' },
  { value: 'Closed', label: 'Closed' },
  { value: 'Rejected', label: 'Rejected' },
  { value: 'Duplicate', label: 'Duplicate' }
];

/** @type {{ value: string, label: string }[]} */
export const CASE_TYPES = [
  { value: '', label: 'Select Type' },
  { value: 'Question', label: 'Question' },
  { value: 'Incident', label: 'Incident' },
  { value: 'Problem', label: 'Problem' }
];

/** @type {{ value: string, label: string }[]} */
export const PRIORITIES = [
  { value: 'ALL', label: 'All Priorities' },
  { value: 'High', label: 'High' },
  { value: 'Medium', label: 'Medium' },
  { value: 'Low', label: 'Low' }
];

/** @type {{ value: string, label: string }[]} */
export const TASK_STATUSES = [
  { value: 'ALL', label: 'All Statuses' },
  { value: 'New', label: 'New' },
  { value: 'In Progress', label: 'In Progress' },
  { value: 'Completed', label: 'Completed' }
];

/** @type {{ value: string, label: string }[]} */
export const OPPORTUNITY_TYPES = [
  { value: '', label: 'Select Type' },
  { value: 'NEW_BUSINESS', label: 'New Business' },
  { value: 'EXISTING_BUSINESS', label: 'Existing Business' },
  { value: 'RENEWAL', label: 'Renewal' },
  { value: 'UPSELL', label: 'Upsell' },
  { value: 'CROSS_SELL', label: 'Cross-sell' }
];

/** @type {{ value: string, label: string }[]} */
export const OPPORTUNITY_SOURCES = [
  { value: '', label: 'Select Source' },
  { value: 'NONE', label: 'None' },
  { value: 'CALL', label: 'Call' },
  { value: 'EMAIL', label: 'Email' },
  { value: 'EXISTING CUSTOMER', label: 'Existing Customer' },
  { value: 'PARTNER', label: 'Partner' },
  { value: 'PUBLIC RELATIONS', label: 'Public Relations' },
  { value: 'CAMPAIGN', label: 'Campaign' },
  { value: 'WEBSITE', label: 'Website' },
  { value: 'OTHER', label: 'Other' }
];

/** @type {{ value: string, label: string }[]} */
export const CURRENCY_CODES = [
  { value: '', label: 'Select Currency' },
  { value: 'USD', label: 'USD - Dollar' },
  { value: 'EUR', label: 'EUR - Euro' },
  { value: 'GBP', label: 'GBP - Pound' },
  { value: 'INR', label: 'INR - Rupee' },
  { value: 'CAD', label: 'CAD - Dollar' },
  { value: 'AUD', label: 'AUD - Dollar' },
  { value: 'JPY', label: 'JPY - Yen' },
  { value: 'CNY', label: 'CNY - Yuan' },
  { value: 'CHF', label: 'CHF - Franc' },
  { value: 'SGD', label: 'SGD - Dollar' },
  { value: 'AED', label: 'AED - Dirham' },
  { value: 'BRL', label: 'BRL - Real' },
  { value: 'MXN', label: 'MXN - Peso' }
];

/** @type {Record<string, string>} */
export const CURRENCY_SYMBOLS = {
  USD: '$',
  EUR: '€',
  GBP: '£',
  INR: '₹',
  CAD: 'CA$',
  AUD: 'A$',
  JPY: '¥',
  CNY: '¥',
  CHF: 'CHF',
  SGD: 'S$',
  AED: 'د.إ',
  BRL: 'R$',
  MXN: 'MX$'
};
