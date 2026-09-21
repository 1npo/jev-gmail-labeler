# About Jev

This is my summary of what Jev is and how it works, based on my reading of the docs and people's discussions about it on Hacker News.

Current as of: 2026-09-20

## Summary

Jev is a general-purpose classifier with frontier intelligence that is very fast and very cheap, and has a nice API. Context window is unknown but speculated to be 32k.

Your application sends Jev two things in each request:

* **State** - Unstructured text or some JSON content you want to classify
* **Questions** - A set of questions to ask about the given state

You can ask 3 types of questions, called primitives. Each question is defined by the type of answer Jev gives you:

* Yes or no (called a "**Noul**", short for Bernoulli random variable)
* **Choice** from a list
* **Score** on a rubric

Jev answers with the probability (0 to 1) that the answer is:

* Yes
* Each given choice
* Each level on the given rubric

The noul, choice, or score with the highest probability is Jev's answer to your question.

You slot Jev's answers into your app's control-flow so it can make decisions about them:

* Nouls slot into "if" statements
* Choices slot into pattern matching ("match/case") statements
* Scores slot into threshold ladders ("if/elif/else" statements)

Your app can also choose to reject or re-route answers if their probability is below a certain threshold. This is helpful in sensitive or high-risk use cases where you only want to accept answers that have a high probability of being correct.

Here's an informative demo that shows how Jev works in practice: https://docs.typesafe.ai/demos/smart-home

Here are example requests and responses for each primitive:

* https://docs.typesafe.ai/primitives/noul
* https://docs.typesafe.ai/primitives/choice
* https://docs.typesafe.ai/primitives/score
