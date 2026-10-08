# Fun facts for music videos

While a music video plays, the wall can pop up "fun facts" over the album art, in
the style of VH1's *Pop-Up Video*. Marks on the progress bar show when the next
one is coming. This page covers where the facts come from and how to get them
into Plex. How the wall shows them is in
[ARCHITECTURE.md](ARCHITECTURE.md) ("Fun-fact bubbles").

## Where the wall reads them

The facts live in each video's **Plex summary**, one fact per line. Nothing else
is needed: there is no setting to turn on and no file on the Pi.

- Blank lines are ignored, and leading and trailing spaces are trimmed.
- The wall uses at most 10 facts per video (`MAX_FACTS` in `proxy/app.py`).
- A video with an empty summary simply has no bubbles.
- Edits are picked up the next time the video plays. The proxy caches facts by
  the item's `updatedAt`, which Plex bumps on every edit.
- **Lock the summary.** Plex can overwrite an unlocked summary on a metadata
  refresh. A locked one survives even `refresh?force=1`.

## Timing

The first bubble comes 25 s in. After that they are at least 35 s apart, and on a
long song they spread out so the facts run until near the end. Each fact shows
up to twice, and nothing starts in a song's last 15 s.

Each bubble stays up for 3 s plus about 12 characters a second, at most 20 s.
Three to five facts is a good number for a typical song.

## Writing good facts

The facts that work best are the ones a viewer can connect to what's on screen.

- **Behind the scenes.** Who's in the video, where it was shot, what went wrong,
  and what a prop or scene means. Skip chart positions, awards and credits.
- **Short.** Aim for under 90 characters and keep every fact under 110. Long
  ones wrap into a big balloon that covers the art.
- **Accurate.** Take each fact from a source you can point to. Reword freely, but
  never change a quote, and keep quotes attributed to the right person.
- **Watch for timing claims.** "At the end of the video" is easy to get wrong
  from a written source. Check the video when a fact says *when* something happens.
- **Family-friendly**, if the wall is somewhere kids see it.
- A *Pop-Up Video* tag ("Cameo alert:", "Listen closely:") is fun now and then,
  but not on every fact.
- **A song with no good facts can have none.** That's better than filler.

Good sources, in order: [Songfacts](https://www.songfacts.com/), the song's
Wikipedia article (the "Background" and "Music video" sections), then
interviews with the artist or director.

### Using an AI assistant to research

An assistant that can browse saves a lot of time, but have it show its
sources. A prompt that works:

> Find 3-5 behind-the-scenes fun facts about the music video for "*Song*" by
> *Artist*, in the style of VH1's Pop-Up Video. Use Songfacts and Wikipedia
> first. Each fact under 110 characters, family-friendly, no chart or award
> trivia. For each, give the source URL and the exact sentence from the page it
> came from. Don't change any quotes.

Then check every fact against its quoted sentence before it goes in. Assistants
sometimes mix up details or invent them, especially quotes and when something
happens in the video.

## Putting them in Plex

### By hand (Plex Web)

1. Open the music video in Plex Web and choose **Edit** (the pencil icon).
2. On the **General** tab, paste the facts into **Summary**, one per line.
3. **Save Changes.** Plex locks a field you edit by hand; the lock icon next to
   Summary should now be closed.

### With the Plex API (many videos at once)

Each video is one request. You need:

- the video's `ratingKey`: it's the number at the end of the item's URL in Plex
  Web, after `key=%2Flibrary%2Fmetadata%2F`
- the library's section id: the number after `source=` when the library is open
- your Plex token. It's a secret, so keep it out of scripts you share.

`type=1` is right for a Movies or Other Videos library. Send the summary with
real line breaks between facts; the URL encoding turns them into `%0A`.

PowerShell:

```powershell
$plex      = 'http://PLEX-SERVER:32400'  # your Plex server
$token     = $env:PLEX_TOKEN
$section   = 'SECTION-ID'                # your music video library's section id
$ratingKey = 'RATING-KEY'                # the video's ratingKey

$facts = @(
  'Cameo alert: the drummer''s dog appears in the final shot.',
  'The whole video was filmed in one take on a borrowed iPhone.'
)
$summary = [uri]::EscapeDataString($facts -join "`n")
$url = "$plex/library/sections/$section/all?type=1&id=$ratingKey" +
       "&summary.value=$summary&summary.locked=1"
Invoke-RestMethod -Method Put -Uri $url -Headers @{ 'X-Plex-Token' = $token }
```

curl (`--data-urlencode` with `-G` builds the same query string):

```sh
curl -sS -X PUT -G "http://PLEX-SERVER:32400/library/sections/SECTION-ID/all" \
  -H "X-Plex-Token: $PLEX_TOKEN" \
  --data-urlencode "type=1" --data-urlencode "id=RATING-KEY" \
  --data-urlencode "summary.locked=1" \
  --data-urlencode "summary.value=$(printf '%s\n%s' \
    'Cameo alert: the drummer’s dog appears in the final shot.' \
    'The whole video was filmed in one take on a borrowed iPhone.')"
```

Then read it back to make sure it stuck. Plex occasionally drops a write while
it's busy, so retry once if the summary comes back empty:

```powershell
(Invoke-RestMethod -Uri "$plex/library/metadata/$ratingKey" -Headers @{ 'X-Plex-Token' = $token; Accept = 'application/json' }).MediaContainer.Metadata[0].summary
```

Before changing summaries in bulk, save the old ones (the same read for each
video) so you can put them back.

### Removing facts

Clear the summary in Plex Web, or send `summary.value=` (empty) with
`summary.locked=0` to hand the field back to Plex's agent.
