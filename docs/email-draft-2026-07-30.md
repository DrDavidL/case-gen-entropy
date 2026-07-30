# Draft email to Cory and Alex

**Subject:** Final Orders and Oracle panel are live for testing, plus one decision we need from you

---

Cory, Alex,

The Final Orders work and the Oracle reference panel are built and deployed. Both the generator
and the simulator are running the new code as of today. Below is what changed, the one decision we
need before we generate any real data, and what would be most useful for each of you to test.

## What your feedback changed

Cory, five of your answers went straight into the build:

- **The Oracle panel runs only for Final Orders, and only when a case has them.** This is enforced
  in code rather than by convention. A case with no Final Orders gets no panel, and the system
  refuses rather than producing an empty distribution.
- **The panel weighs cost of commission.** Every rater is now explicitly instructed that burden and
  risk of acting count, not just the risk of missing something, and that a high burden action needs
  correspondingly strong justification. Your brain biopsy example is close to the wording we used.
- **Reasoning effort set to medium**, per your note that we do not need the highest setting.
- **The name stays "Final Orders."** We recorded why, so it does not get reopened later: "Key
  Management Decisions" would be confused with the simulator's separate three next steps box, which
  is a different instrument.
- **The otolaryngologist seat is now an "applicable specialty surgeon or subspecialist"** set per
  case, so a cardiac case gets a cardiac subspecialist rather than an ENT.

We also split the panel across two model families. On the first live run, three of the emergency
medicine panelists returned identical ratings with nearly identical wording, which is the failure
mode we flagged in the proposal showing up immediately. Panelists whose roles are near duplicates
of each other now run on a different model, so their agreement means something. The two panelists
who represent the stewardship versus risk averse axis deliberately stay on the same model, because
putting them on different models would make any difference between them uninterpretable.

## The one decision we need

Alex, this one is yours. Cory asked to see any proposed change to the rating stem before adopting
it, which is right, so nothing is settled.

Your original wording:

```
Considering the information available in this case, the appropriateness of
ordering a brain MRI is:

  -2 = Strongly inappropriate
  -1 = Probably inappropriate
   0 = Uncertain / depends on additional information
  +1 = Reasonable / defensible
  +2 = Strongly indicated
```

What we propose instead:

```
Based on the information you gathered during this encounter, and before any
pending results return, ordering a brain MRI now would be:

  -2 = Clearly inappropriate
  -1 = Probably inappropriate
   0 = Equally appropriate to order or not to order
  +1 = Probably appropriate
  +2 = Clearly appropriate

  [ ] My rating would change substantially with information I was not able
      to obtain during this encounter.
```

Four changes, and the reasoning behind each:

1. **Whose information is stated explicitly.** The learner rates on what they actually elicited;
   the reference panel rates on the full record. Comparing the two is only meaningful if each knew
   what they were conditioning on.
2. **The decision is anchored in time.** Appropriateness of imaging depends heavily on when it is
   ordered. Without a stated timepoint, raters silently assume different ones and disagree for
   reasons unrelated to clinical judgment.
3. **One construct across the scale.** The original moves from inappropriateness at the bottom to
   defensibility at +1 and indication at +2. A rater who thinks an MRI is defensible but not
   necessary is being asked to collapse two different ideas.
4. **The midpoint is narrowed** to true equipoise, with information deficit moved to its own
   checkbox. On a five point scale, a broad and attractive midpoint pulls mass away from the
   discriminating ends.

Both versions are implemented and switching between them is a one line configuration change, so
there is no cost to telling us you prefer the original. The cost is only in waiting: the stem
determines what the panel is asked, so changing it after we generate distributions invalidates
them. **We are holding off on generating any real Oracle data until we hear from you.** Nothing is
lost by waiting, since no case carries Final Orders yet.

## What would help most to test

Everything below happens inside the apps, so there is nothing to install.

### Generating a case (Cory, and Alex if you want to see the item wording in context)

1. **Generate a case** as you normally would. Nothing about that step changed.
2. **Open the Final Orders section** in the Edit tab and click **Propose Final Orders**. The
   generator suggests three to five candidate actions and writes nothing until you accept one.
   - The thing worth judging: **are the suggestions actually debatable?** Each comes with a note
     saying why clinicians would disagree. If a suggestion is one that every clinician would rate
     +2, it makes a useless item, and telling us that is genuinely useful feedback.
3. **Accept one, edit its wording, and delete another.** All three should behave. Deleting should
   remove the one you clicked, not the last one in the list.
4. **Read the rendered item shown under each order.** This is the exact sentence a student sees. If
   it reads awkwardly, that is a wording bug worth reporting, particularly for anything that is not
   literally an order, such as activating the stroke team.
5. **Check the suppression synonyms.** These are the phrasings the simulator will recognise and
   withhold the result for. If a student could plausibly type something not on the list, add it.
   An incomplete list means the student sees the result before rating it, which destroys the
   measurement for that case.
6. **Set the specialty seat** if the case calls for one. This decides which subspecialist sits on
   the panel.
7. **Save with "Run the Oracle panel after saving" ticked**, then wait three to five minutes and
   press Refresh. You should see, per order, a distribution across the five ratings, the level of
   agreement, and a plain-language flag telling you whether the item will separate learners.
   - The flags are the point of the whole exercise. **"Low discrimination" means the item is not
     worth using.** Seeing that before students run the case is exactly what we wanted.
8. **Try editing the case content and re-running.** The panel will refuse, on the grounds that it
   would otherwise rate a version of the case the student will not see. There is a button to
   re-read the edited content and rebuild the record, after which the panel runs again. We would
   like to know whether that refusal reads as helpful or merely obstructive.

You can also expand **"What the panel sees"** at any point. It shows the exact blinded record the
raters get, which excludes the diagnosis, your reasoning notes, and the teaching points. It is the
fastest way to satisfy yourself that the panel is not being handed the answer.

### Running a simulation (both of you)

1. **Run a case with orders disabled.** Previously this trapped you in the interview with no way
   forward, which is a bug we found while working on this. It should now go straight from the
   interview to clinical reasoning, with a note that orders are off for this case.
2. **Run a voice case, end the call, then start a second one.** Both conversations should appear in
   your transcript. Previously the first was lost unless you retrieved it at exactly the right
   moment, which is easy to get wrong and gives no sign that anything went missing.
3. **Advance without pressing Retrieve Transcript.** It should still capture everything: we take a
   final sweep as you leave the interview.
4. **Download both PDFs at the end and check the submission code matches on each.** There is now a
   clearer warning telling students to use the download buttons rather than copying text off the
   screen, which is what one tester did.
5. **If the case has Final Orders**, confirm that ordering one during the encounter returns a
   pending message rather than a result, and that a near miss is not caught by mistake. Ordering an
   MRI of the lumbar spine should behave normally when the Final Order is a brain MRI.

## Other items

- **ElevenLabs does not retain audio on our account**, which closes the largest privacy risk we had
  open. The agent settings independently disable recording as well.
- **UNMC holding IRB approval and being the only source of student identities** resolves the two
  governance questions. We would still like the data use agreement to record in writing that we
  never receive the code to student key, since that clause is what keeps our data de-identified.
- **Still open from the original list:** whether to schedule a small human expert panel on two or
  three cases. We continue to recommend it. Model derived distributions are labeled as such
  everywhere, but agreement between models and clinicians is a study to run rather than an
  assumption, and reviewers will ask.

Happy to walk either of you through any of it live.

David
