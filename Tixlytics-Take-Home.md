Tixlytics
Founding Engineer — Take-Home
What we do
We build automated trading systems for the secondary ticket market. Through a fan-friendly program on a major primary marketplace, we buy tickets from fans who can’t attend and resell them across secondary exchanges at a margin. The interesting part is deciding what to buy and at what price — that’s what this exercise is about.
The exercise
Attached is events.json — market data for three live events (concert, theater, sports). Each event includes secondary listings, primary availability, sales data (ATP, daily volume, inventory), and current competitor bids. Field definitions are in the _schema object.
Build a bid engine that reads this file and decides, for each event and section: whether to bid, how much to bid, and how much capital to put at risk. Your objective is to maximize risk-adjusted profits. You have $50,000 in total capital. Keep in mind: what sellers are asking and what tickets actually sell for are not the same thing.
Bids are placed at the section level — a bid applies to any ticket in that section regardless of row. Assume 10% all-in resale costs (platform fees, payment processing, fulfillment). Section naming varies across data sources; you’ll need to normalize it.
Not every event deserves a bid. Not every section within a good event deserves a bid. Part of this exercise is figuring out which is which.
What to submit
•	Your script — single file, any language. Reads events.json, outputs bid decisions to stdout. Your script should work on any events file with the same schema. Make it clear what you’re bidding on and why.
•	A short write-up — your approach, trade-offs, where AI helped and where you corrected it, and what data you’d want for a production system.
•	Your AI chat history — export, screenshots, or summary. We want to see how you decomposed the problem and where you corrected the AI.
Use AI tools. We expect it. We’re testing whether you can direct an AI toward a good solution to an ambiguous problem and catch where it gets things wrong.
Logistics
•	Most candidates spend about an hour. Don’t overthink it.
•	Deadline: Thursday, March 12th by 11:59pm EST
•	Submit: private repo link to sri@tixlytics.com.
•	Ambiguity: if something isn’t specified, make a call and explain it. That’s part of the test.
Good luck. We’re a small team building something real, and this problem is a genuine slice of what we work on every day.
