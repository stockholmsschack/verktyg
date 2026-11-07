#let data = json("dispensations.json")
#set document(title: "Spelardispenser JGP " + str(data.year))
#set page(paper: "a4", margin: 2cm)
#set text(font: "Roboto", size: 11pt, lang: "sv")
#image("stsf.png", width: 5cm)
#v(1cm)
#text(size: 20pt, weight: "bold", fill: rgb("#0072B2"))[Spelardispenser JGP #data.year]
#v(0.3em)
#data.date
#v(1cm)
#table(
  columns: (1fr, auto, auto),
  inset: 9pt,
  stroke: none,
  fill: (_, y) => if y == 0 { rgb("c9daf8") } else if calc.even(y) { rgb("efefef") },
  align: (left, center, center),
  table.header([*Namn*], [*Födelseår*], [*Spelklass-år*]),
  ..data.players.map(player => (
    player.name, str(player.birth_year), str(player.plays_as),
  )).flatten(),
)
