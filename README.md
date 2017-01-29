Appraise
========

This is my own fork of [Appraise](http://github.com/cfedermann/Appraise) used to evaluate Czech NLG. 
Everything still works as in the original, so please refer to the [README file there](https://github.com/cfedermann/Appraise/blob/master/README.md).

My changes
----------

### Czech NLG specific

* Interface translated into Czech
* Instructions on the intro page
    * Incl. an overlay example image
* Invite codes are retrieved automatically via JS
* Assignment to groups automatically via JS
* Source (dialogue act) formatting


### General

* Colors in ranking (from green to red)
* Animated ranking -- the sentences sort themselves automatically
* User can choose their password
* `views.py` (both general and under `cs_rest`) now use 
  `from __future__ import unicode_literals` to prevent crashes if users create
  usernames with accented characters
* Less confusing buttons layout in ranking
