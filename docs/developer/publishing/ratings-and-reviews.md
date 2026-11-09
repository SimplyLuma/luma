---
title: Ratings and reviews
summary: Ratings and reviews are not live yet. How they will work in Depot and on the web, how the rating is calculated, replying as a developer, reporting, and what is not allowed.
order: 8
section: Publishing
---

# Ratings and reviews

Ratings and reviews live in Depot on Luma.

> **Status: not live yet.** Depot has a place for ratings
> and reviews, and the Hub service behind them is built, but it is not
> connected to the live Hub yet. Until it is, no app shows ratings and nobody
> can post a review. Replying as a developer also needs the developer portal,
> which is not open yet.

## How reviews work

- **One review per Luma Connect account per app**, with a rating from 1 to 5
  stars, an optional title (up to 100 characters) and an optional body (up to
  4,000). People can edit or delete their review.
- Each review is tied to the **app version** it was written against, so you
  and readers can see whether it describes the current release.
- Posting needs a Luma Connect account with a **verified email**. On Luma,
  Depot posts through the device’s Luma Connect sign-in; on the web, people
  sign in at `hub.simplyluma.com`. Browsing, searching and installing never
  need an account.
- A review posted from Depot on a Luma device where the app is installed
  carries an **“Installed on Luma”** label. A review from the web never does.
- A review from an account less than **24 hours** old waits for moderation
  before it appears.
- Only `public` and `unlisted` apps take reviews.

## How the rating is calculated

The rating shown is the **average of visible reviews**, with the number of
ratings beside it. An app with **fewer than 3 ratings** shows “Not enough
ratings” instead of an average. Reviews waiting for moderation, hidden and
removed reviews do not count.

## Replying as a developer

You can reply **publicly, once per review**, from **Your apps** in the
developer portal, and edit your reply later. It appears under the review as a
response from your developer name.

Good replies:

- thank the person and address the specific problem;
- say whether a fix is released or planned, and in which version;
- point to your support link for anything that needs back-and-forth;
- stay polite, even when the review is not.

Do not ask for personal information, argue, or offer anything in exchange for
changing a rating.

## Reporting

Anyone signed in can report a review once, with a reason. You cannot report
your own. Reports go to Luma’s moderators, who can hide or remove a review. As
a developer, report reviews that:

- contain personal information, harassment or hate;
- are spam or advertising;
- are about a different app;
- are clearly written by someone with a conflict of interest.

Do not report a review only because it is negative. Negative reviews that
describe a real experience stay.

## Rules for reviews

Reviews must be about the app and the person’s experience with it. Moderators
remove reviews that:

1. contain personal information about anyone;
2. harass, threaten or use hate speech;
3. are spam, advertising or links to unrelated content;
4. are fake, bought, incentivized, or written by the developer or someone acting
   for them;
5. are about a different app, or only about pricing policies of another store;
6. contain illegal content.

## What developers must not do

Under [review guideline 3.5](review-guidelines.md#3-honesty), you must not buy,
trade or incentivize reviews, write reviews of your own apps or competitors’
apps, or ask people to leave a review only if they are happy. You may ask for a
review in general, at a sensible moment, occasionally.

## API

Depot and the web will use these Hub endpoints. They are listed for
transparency; they are not connected yet and are not a supported API for other
clients.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/depot/apps/<slug>/reviews?cursor=` | List reviews, 20 a page |
| `PUT` | `/api/depot/apps/<slug>/review` | Create or edit your review |
| `DELETE` | `/api/depot/apps/<slug>/review` | Delete your review |
| `POST` | `/api/depot/reviews/<id>/report` | Report a review |
| `POST` | `/api/depot/reviews/<id>/reply` | Reply as the developer |
