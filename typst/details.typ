#set page(
  margin: 0.8cm,
  footer: context[
    #set text(8pt, font: "Roboto")
    #set align(center)
    #counter(page).display(
    "1 / 1", both: true)],
  )
#set text(9pt, font: "Roboto", weight: "regular")
#show heading.where(): it => text(fill: rgb("#0072B2"), it.body)

#show text.where(lang: "sv"): it => {
  set text(weight: 400, style: "italic", fill: maroon)
  it
}
#show text.where(lang: "en"): it => {
  set text(weight: 400, fill: black)
  it
}

#show link: it => underline(
  stroke: 0.75pt,
  offset: 3pt,
  it
)

#include "rules.typ"
